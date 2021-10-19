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
        self.smooth_troughput = None
        self.d = 30

        self.buffer_sizes = [0]
        self.buff_size_danger = 15
        self.buff_max = self.whiteboard.get_max_buffer_size()

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
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_xml_response(self, msg):
        parsed_mpd = parse_mpd(msg.get_payload())
        self.qi = parsed_mpd.get_qi()
        t = time.perf_counter() - self.request_time
        self.throughputs.append((msg.get_bit_length() / t, time.perf_counter()))
        self.send_up(msg)

    def handle_segment_size_request(self, msg):
        self.buffer_sizes.append(self.whiteboard.get_amount_video_to_play())
        # pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()
        # pbs = self.whiteboard.get_playback_buffer_size()
        # self.update_troughputs()
        # self.print_throughputs()
        # self.update_troughputs()

        # self.print_buffer_times()
        # self.print_buffer_sizes()
        if len(self.throughputs) >= 10:
            avg_throughput = mean(self.throughputs[-10:][0]) / 2
        else:
            avg_throughput = mean(self.throughputs[:][0]) / 2

        if self.smooth_troughput is None:
            self.smooth_troughput = self.throughputs[-1][0]
        self.smooth_troughput = 0.2 * self.smooth_troughput + 0.8 * avg_throughput

        current_buffer_size = self.whiteboard.get_amount_video_to_play()
        buffering_size_diff = self.buffer_sizes[-1] - self.buffer_sizes[-2]
        print("buffering_size_diff =", buffering_size_diff)

        self.FDASH.input['buff_size'] = current_buffer_size
        self.FDASH.input['buff_size_diff'] = buffering_size_diff
        self.FDASH.input['rate'] = avg_throughput / self.qi[self.current_qi_index]
        self.FDASH.compute()

        factor = self.FDASH.output['factor']
        print(">>>>> Fator de acréscimo/decréscimo =", factor)

        current_quality_id = self.qi[self.current_qi_index]
        print(f"CURRENT QUALITY ID: {current_quality_id} bps")

        # Pegar a media dos k ultimos throughtputs e multiplicar por fator
        desired_quality_id = avg_throughput * factor
        print(f"DESIRED QUALITY ID: {int(desired_quality_id)} bps")

        predicted_buff_size = current_buffer_size + (self.smooth_troughput / desired_quality_id) - 1

        if desired_quality_id > current_quality_id:
            if current_buffer_size <= self.buff_size_danger:
                desired_quality_id = current_quality_id

        if desired_quality_id < current_quality_id:
            if(predicted_buff_size >= self.buff_max / 2):
                desired_quality_id = current_quality_id

        # Descobrir maior qualidade mais proximo da qualidade desejada
        for i in range(len(self.qi)):
            if desired_quality_id >= self.qi[i]:
                self.current_qi_index = i
            else:
                break

        # Nos primeiros segmentos, escolher a menor qualidade possível..
        msg.add_quality_id(self.qi[self.current_qi_index])

        playback_pauses = self.whiteboard.get_playback_pauses()
        print("PAUSES:", len(playback_pauses))
        print("SEGMENT ID:", msg.get_segment_id())
        print(f"CHOSEN QUALITY: {msg.get_quality_id()}bps")

        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        t = time.perf_counter() - self.request_time
        self.throughputs.append((msg.get_bit_length() / t, time.perf_counter()))
        self.send_up(msg)

    def print_throughputs(self):
        print("-----------------------------------------")
        print(f"THROUGHPUTS: {self.throughputs} >>>> LEN: {len(self.throughputs)}")
        if len(self.throughputs) >= 1:
            print(f"AVG THROUGHPUT: {int(mean(self.throughputs[:][0]))} Mbps")
        print("-----------------------------------------")

    def print_buffer_times(self):
        pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()
        print("-----------------------------------------")
        print(f"BUFFER TIMES: {pbt} >>>> LEN: {len(pbt)}")
        if len(pbt) >= 1:
            print(f"AVG BUFFER TIME: {int(mean(pbt))}s")
        print("-----------------------------------------")

    def print_buffer_sizes(self):
        pbs = self.whiteboard.get_playback_buffer_size()
        print("-----------------------------------------")
        print(f"BUFFER SIZES: {pbs} >>>> LEN: {len(pbs)}")
        if len(pbs) >= 1:
            print(f"AVG BUFFER SIZE: {int(mean(x[1] for x in pbs))}")
        print("-----------------------------------------")

    def update_troughputs(self):
        current_time = time.perf_counter()
        while (current_time - self.throughputs[0][1] > self.d):
            self.throughputs.pop(0)

    def set_buffering_size_membership(self):
        buff_size_danger = self.buff_size_danger
        buff_max = self.buff_max
        buff_size = ctrl.Antecedent(np.arange(0, buff_max+0.1, 0.1), 'buff_size')

        # Buffer Size
        buff_size['D'] = fuzz.trapmf(buff_size.universe, [0, 0, buff_size_danger, buff_max/2])
        buff_size['L'] = fuzz.trimf(buff_size.universe, [buff_size_danger, buff_max/2, 3*buff_max/4])
        buff_size['S'] = fuzz.trapmf(buff_size.universe, [buff_max/2, 3*buff_max/4, np.inf, np.inf])
        self.buff_size = buff_size

    def set_buffering_size_diff_membership(self):
        buff_size_diff = ctrl.Antecedent(np.arange(-3, 3.1, 0.1), 'buff_size_diff')

        # Diferencial do Buffer Sizer
        buff_size_diff['F'] = fuzz.trapmf(buff_size_diff.universe, [-3, -3, -2, 0])
        buff_size_diff['S'] = fuzz.trimf(buff_size_diff.universe, [-2, 0, 2])
        buff_size_diff['R'] = fuzz.trapmf(buff_size_diff.universe, [0, 2, np.inf, np.inf])
        self.buff_size_diff = buff_size_diff

    def set_rate_membership(self):
        rate = ctrl.Antecedent(np.arange(0, 2.6, 0.1), 'rate')

        # Taxa de bits
        rate['L'] = fuzz.trapmf(rate.universe, [0, 0, 0.8, 1.2])
        rate['S'] = fuzz.trimf(rate.universe, [0.8, 1.2, 2])
        rate['H'] = fuzz.trapmf(rate.universe, [1.2, 2, np.inf, np.inf])
        self.rate = rate

    def set_factor_membership(self):
        # Fator de qualidade varia de 0 a 2, com precisão de 0.05
        N2 = 0.25   # Reduzir - R
        N1 = 0.5    # Reduzir pouco - SR
        Z = 1       # Não alterar - NC
        P1 = 1.5    # Aumentar pouco - SI
        P2 = 2      # Aumentar - I
        factor = ctrl.Consequent(np.arange(0, P2 + 0.55, 0.05), 'factor')

        # Fator de incremento/decremento da qualidade do próximo segmento
        factor['R'] = fuzz.trapmf(factor.universe, [0, 0, N2, N1])
        factor['SR'] = fuzz.trimf(factor.universe, [N2, N1, Z])
        factor['NC'] = fuzz.trimf(factor.universe, [N1, Z, P1])
        factor['SI'] = fuzz.trimf(factor.universe, [Z, P1, P2])
        factor['I'] = fuzz.trapmf(factor.universe, [P1, P2, np.inf, np.inf])
        self.factor = factor

    def set_controller_rules(self):
        # Buffer Dangerous
        rule1 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['F'] & self.rate['L'], self.factor['R'])
        rule2 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['F'] & self.rate['S'], self.factor['R'])
        rule3 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['F'] & self.rate['H'], self.factor['R'])

        rule4 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['S'] & self.rate['L'], self.factor['R'])
        rule5 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['S'] & self.rate['S'], self.factor['SR'])
        rule6 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['S'] & self.rate['H'], self.factor['SR'])

        rule7 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['R'] & self.rate['L'], self.factor['R'])
        rule8 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['R'] & self.rate['S'], self.factor['SR'])
        rule9 = ctrl.Rule(self.buff_size['D'] & self.buff_size_diff['R'] & self.rate['H'], self.factor['SR'])

        # Buffer Low
        rule10 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['F'] & self.rate['L'], self.factor['SR'])
        rule11 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['F'] & self.rate['S'], self.factor['NC'])
        rule12 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['F'] & self.rate['H'], self.factor['NC'])

        rule13 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['S'] & self.rate['L'], self.factor['NC'])
        rule14 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['S'] & self.rate['S'], self.factor['NC'])
        rule15 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['S'] & self.rate['H'], self.factor['NC'])

        rule16 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['R'] & self.rate['L'], self.factor['NC'])
        rule17 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['R'] & self.rate['S'], self.factor['NC'])
        rule18 = ctrl.Rule(self.buff_size['L'] & self.buff_size_diff['R'] & self.rate['H'], self.factor['SI'])

        # Buffer Safe
        rule19 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['F'] & self.rate['L'], self.factor['SI'])
        rule20 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['F'] & self.rate['S'], self.factor['SI'])
        rule21 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['F'] & self.rate['H'], self.factor['I'])

        rule22 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['S'] & self.rate['L'], self.factor['SI'])
        rule23 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['S'] & self.rate['S'], self.factor['SI'])
        rule24 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['S'] & self.rate['H'], self.factor['I'])

        rule25 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['R'] & self.rate['H'], self.factor['I'])
        rule26 = ctrl.Rule(self.buff_size['S'] & self.buff_size_diff['R'] & self.rate['H'], self.factor['I'])

        self.rules = [
            rule1, rule2, rule3, rule4, rule5, rule6, rule7, rule8, rule9,
            rule10, rule11, rule12, rule13, rule14, rule15, rule16, rule17, rule18,
            rule19, rule20, rule21, rule22, rule23, rule24, rule25, rule26
        ]

    def initialize(self):
        pass

    def finalization(self):
        pass
