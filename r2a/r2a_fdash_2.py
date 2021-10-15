from r2a.ir2a import IR2A
from player.parser import *
from statistics import mean
from skfuzzy import control as ctrl
import time
import numpy as np
import skfuzzy as fuzz


class R2A_FDASH_2(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        self.qi = []
        self.throughputs = []
        self.request_time = 0
        self.current_qi_index = 0
        self.d = 60

        ## Controlador FDASH ##

        # Tempo de buffering Alvo
        self.T = 8
        self.b_danger = 3
        # Buffering size
        self.set_buffering_size_membership()
        # Buffering size difference
        self.set_buffering_size_diff_membership()
        # Throughput
        self.set_rate_membership()
        # Factor
        self.set_factor_membership()
        # Configura controlador
        self.set_controller_rules()
        self.FDASHControl = ctrl.ControlSystem(self.rules)
        self.FDASH = ctrl.ControlSystemSimulation(self.FDASHControl)

    def handle_xml_request(self, msg):
        self.send_down(msg)

    def handle_xml_response(self, msg):
        parsed_mpd = parse_mpd(msg.get_payload())
        self.qi = parsed_mpd.get_qi()
        self.send_up(msg)

    def handle_segment_size_request(self, msg):
        pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()
        pbs = self.whiteboard.get_playback_buffer_size()

        if(len(pbs) > 1):
            playback_qi = self.whiteboard.get_playback_qi()
            print("playback_qi:", playback_qi)
            self.print_info()
            rd = self.get_rd()

            buffering_size = pbs[-1][1]
            buffering_size_diff = buffering_size - pbs[-2][1]
            print("buffering_size_diff = ", buffering_size_diff)

            self.FDASH.input['Buffering size B'] = buffering_size
            self.FDASH.input['Buffering size diff ΔB (b)'] = buffering_size_diff
            self.FDASH.input['Bit Rate'] = self.throughputs[-1][0] / self.qi[self.current_qi_index]

            self.FDASH.compute()

            factor = self.FDASH.output['Factor']
            print(">>>>> Fator de acréscimo/decréscimo =", factor)

            current_quality_id = self.qi[self.current_qi_index]
            print(f"CURRENT QUALITY ID: {current_quality_id}bps")

            # Pegar a media dos k ultimos throughtputs e multiplicar por fator
            desired_quality_id = rd * factor
            print(f"DESIRED QUALITY ID: {int(desired_quality_id)}bps")

            # Descobrir menor qualidade mais proximo de: fator vezes a media dos throughtputs
            for i in range(len(self.qi)):
                if desired_quality_id >= self.qi[i]:
                    self.current_qi_index = i
                else:
                    break

        # Nos primeiros dois segmentos, escolher a menor qualidade possível?
        msg.add_quality_id(self.qi[self.current_qi_index])

        print("SEGMENT ID:", msg.get_segment_id())
        print(f"CHOSEN QUALITY: {msg.get_quality_id()}bps")

        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        t = time.perf_counter() - self.request_time
        self.throughputs.insert(0, (msg.get_bit_length() / t, time.perf_counter()))
        self.send_up(msg)

    def print_info(self):
        pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()
        playback_pauses = self.whiteboard.get_playback_pauses()
        pbs = self.whiteboard.get_playback_buffer_size()

        print("-----------------------------------------")
        print(f"THROUGHPUTS: {self.throughputs}")
        print(f"# THROUGHPUTS: {len(self.throughputs)}")
        print(f"AVG THROUGHPUTS: {int(mean(x[0] for x in self.throughputs))} Mbps")
        print("-----------------------------------------")
        # print(f"BUFFER TIMES: {pbt}")
        print(f"# BUFFER TIME: {len(pbt)}")
        if len(pbt) > 1:
            print(f"AVG BUFFER TIME: {int(mean(pbt))}s")
        print("-----------------------------------------")
        print("PAUSES:", len(playback_pauses))
        print("-----------------------------------------")
        # print("BUFFER SIZES:", pbs)
        print("# BUFFER SIZE:", len(pbs))
        print(f"AVG BUFFER SIZE: {int(mean(x[1] for x in pbs))}")
        print("-----------------------------------------")


    def get_rd(self):
        current_time = time.perf_counter()
        while (current_time - self.throughputs[-1][1] > self.d):
            self.throughputs.pop(-1)

        rd = mean(x[0] for x in self.throughputs)
        # print("RD:", rd)
        return rd


    def set_buffering_size_membership(self):
        T = self.T
        b_danger = self.b_danger
        buff_size = ctrl.Antecedent(np.arange(0, 3*T/4+0.5, 0.5), 'Buffering size B')

        # Buffer Size
        buff_size['dangerous'] = fuzz.trapmf(buff_size.universe, [0, 0, b_danger, T/2])
        buff_size['low'] = fuzz.trimf(buff_size.universe, [b_danger, T/2, 3*T/4])
        buff_size['safe'] = fuzz.trimf(buff_size.universe, [T/2, 3*T/4, T])
        self.buff_size = buff_size

    def set_buffering_size_diff_membership(self):
        T = self.T
        b_danger = self.b_danger
        buff_size_diff = ctrl.Antecedent(np.arange(-3, 3, 1), 'Buffering size diff ΔB (b)')

        # Diferencial do Buffer Sizer
        buff_size_diff['falling'] = fuzz.trapmf(buff_size_diff.universe, [-b_danger, -b_danger, -2, 0])
        buff_size_diff['steady'] = fuzz.trimf(buff_size_diff.universe, [-2, 0, 2])
        buff_size_diff['rising'] = fuzz.trimf(buff_size_diff.universe, [0, 2, 2])
        self.buff_size_diff = buff_size_diff

    def set_rate_membership(self):
        rate = ctrl.Antecedent(np.arange(0, 2.2, 0.2), 'Bit Rate')

        # Taxa de bits
        rate['low'] = fuzz.trapmf(rate.universe, [0, 0, 0.8, 1.2])
        rate['steady'] = fuzz.trimf(rate.universe, [0.8, 1.2, 2])
        rate['high'] = fuzz.trimf(rate.universe, [1.2, 2, 2])
        self.rate = rate

    def set_factor_membership(self):
        # Fator de qualidade varia de 0 a 2, com precisão de 0.01
        factor = ctrl.Consequent(np.arange(0, 2.05, 0.05), 'Factor')
        N2 = 0.25   # Reduzir - R
        N1 = 0.5    # Reduzir pouco - SR
        Z = 1       # Não alterar - NC
        P1 = 1.5    # Aumentar pouco - SI
        P2 = 2      # Aumentar - I

        # Fator de incremento/decremento da qualidade do próximo segmento
        factor['reduce'] = fuzz.trapmf(factor.universe, [0, 0, N2, N1])
        factor['small reduce'] = fuzz.trimf(factor.universe, [N2, N1, Z])
        factor['no change'] = fuzz.trimf(factor.universe, [N1, Z, P1])
        factor['small increase'] = fuzz.trimf(factor.universe, [Z, P1, P2])
        factor['increase'] = fuzz.trimf(factor.universe, [P1, P2, 2])
        self.factor = factor

    def set_controller_rules(self):
        # Buffer Dangerous
        rule1 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['falling'] & self.rate['low'], self.factor['reduce'])
        rule2 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['falling'] & self.rate['steady'], self.factor['reduce'])
        rule3 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['falling'] & self.rate['high'], self.factor['reduce'])

        rule4 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['steady'] & self.rate['low'], self.factor['reduce'])
        rule5 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['steady'] & self.rate['steady'], self.factor['small reduce'])
        rule6 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['steady'] & self.rate['high'], self.factor['small reduce'])

        rule7 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['rising'] & self.rate['low'], self.factor['reduce'])
        rule8 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['rising'] & self.rate['steady'], self.factor['small reduce'])
        rule9 = ctrl.Rule(self.buff_size['dangerous'] & self.buff_size_diff['rising'] & self.rate['high'], self.factor['small reduce'])

        # Buffer Low
        rule10 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['falling'] & self.rate['low'], self.factor['small reduce'])
        rule11 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['falling'] & self.rate['steady'], self.factor['no change'])
        rule12 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['falling'] & self.rate['high'], self.factor['no change'])

        rule13 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['steady'] & self.rate['low'], self.factor['no change'])
        rule14 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['steady'] & self.rate['steady'], self.factor['no change'])
        rule15 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['steady'] & self.rate['high'], self.factor['no change'])

        rule16 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['rising'] & self.rate['low'], self.factor['no change'])
        rule17 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['rising'] & self.rate['steady'], self.factor['no change'])
        rule18 = ctrl.Rule(self.buff_size['low'] & self.buff_size_diff['rising'] & self.rate['high'], self.factor['small increase'])

        # Buffer Safe
        rule19 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['falling'] & self.rate['low'], self.factor['small increase'])
        rule20 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['falling'] & self.rate['steady'], self.factor['small increase'])
        rule21 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['falling'] & self.rate['high'], self.factor['increase'])

        rule22 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['steady'] & self.rate['low'], self.factor['small increase'])
        rule23 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['steady'] & self.rate['steady'], self.factor['small increase'])
        rule24 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['steady'] & self.rate['high'], self.factor['increase'])

        rule25 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['rising'] & self.rate['high'], self.factor['increase'])
        rule26 = ctrl.Rule(self.buff_size['safe'] & self.buff_size_diff['rising'] & self.rate['high'], self.factor['increase'])

        self.rules = [
            rule1, rule2, rule3, rule4, rule5, rule6, rule7, rule8, rule9,
            rule10, rule11, rule12, rule13, rule14, rule15, rule16, rule17, rule18,
            rule19, rule20, rule21, rule22, rule23, rule24, rule25, rule26
        ]

    def initialize(self):
        pass

    def finalization(self):
        pass
