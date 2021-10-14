from r2a.ir2a import IR2A
from player.parser import *
from statistics import mean
from skfuzzy import control as ctrl
import time
import numpy as np
import skfuzzy as fuzz


class R2A_FDASH(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        # guarda qualidades disponíveis
        self.qi = []
        self.current_qi = 0
        self.throughputs = []
        self.request_time = 0

        ## Controlador FDASH variables ##
        
        # Tempo de buffering Alvo
        self.T = 20 
        # Buffering time distance
        self.buff_time = self.set_buffering_time_membership()
        # Buffering time difference
        self.buff_time_diff = self.set_buffering_time_diff_membership()
        # Diferença entre qualidades
        self.quality_diff = self.set_quality_diff_membership()
        self.rules = self.get_controller_rules()
        self.FDASHControl = ctrl.ControlSystem(self.rules)
        self.FDASH = ctrl.ControlSystemSimulation(self.FDASHControl)

    def handle_xml_request(self, msg):
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_xml_response(self, msg):
        parsed_mpd = parse_mpd(msg.get_payload())
        self.qi = parsed_mpd.get_qi()

        self.send_up(msg)

    def handle_segment_size_request(self, msg):
        self.request_time = time.perf_counter()
        # MAX BUFFER SIZE = 60
        # SEGMENT SIZE = 1
        # QUALITY ID = [0, 19]

        print("-----------------------------------------")
        buffer_time_histogram = self.whiteboard.get_playback_segment_size_time_at_buffer()
        playback_pauses = self.whiteboard.get_playback_pauses()
        print("HISTOGRAM:", buffer_time_histogram)
        print("-----------------------------------------")
        print("PAUSES:", len(playback_pauses))
        print("-----------------------------------------")

        if(len(buffer_time_histogram) > 1):
            if len(self.throughputs) >= 10:
                avg_throughput = mean(self.throughputs[-10:])
            else:
                avg_throughput = mean(self.throughputs)

            buffering_time = buffer_time_histogram[-1]
            buffering_time_diff = buffering_time - buffer_time_histogram[-2]
            print("buffering_time_diff = ", buffering_time_diff)

            self.FDASH.input['buff_time'] = buffering_time
            self.FDASH.input['buff_time_diff'] = buffering_time_diff

            self.FDASH.compute()

            factor = self.FDASH.output['quality_diff']
            print("Output: fator de acréscimo/decréscimo =", factor)

            # Pegar a ultima qualidade selecionada e multiplicar por factor
            current_quality_id = self.qi[self.current_qi]
            desired_quality_id = avg_throughput * factor

            print(f"CURRENT QUALITY ID: {current_quality_id}bps")
            print(f"DESIRED QUALITY ID: {desired_quality_id}bps")

            # Descobrir menor qualidade mais proximo de: fator vezes a media dos throughtputs
            for i in range(len(self.qi)):
                if desired_quality_id >= self.qi[i]:
                    self.current_qi = i
                else:
                    break

        # Nos primeiros dois segmentos, escolher a menor qualidade possível?
        msg.add_quality_id(self.qi[self.current_qi])

        print("SEGMENT ID:", msg.get_segment_id())
        print(f"CHOSEN QUALITY: {msg.get_quality_id()}bps")

        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        t = time.perf_counter() - self.request_time
        self.throughputs.append(msg.get_bit_length() / t)
        print("QUANTIDADE DE THROUGHTPUTS:", len(self.throughputs))
        self.send_up(msg)

    def initialize(self):
        pass

    def finalization(self):
        pass

    def set_buffering_time_membership(self):
        T = self.T
        buff_time = ctrl.Antecedent(np.arange(0, 5*T, 1), 'buff_time')

        # Diferença entre tempo de buffering atual com um valor alvo T
        buff_time['S'] = fuzz.trapmf(buff_time.universe, [0, 0, (2*T/3), T])
        buff_time['C'] = fuzz.trapmf(buff_time.universe, [(2*T/3), T, T, 4*T])
        buff_time['L'] = fuzz.trapmf(buff_time.universe, [T, 4*T, 5*T, 5*T])
        return buff_time

    def set_buffering_time_diff_membership(self):
        T = self.T
        buff_time_diff = ctrl.Antecedent(np.arange(-T, 5*T, 1), 'buff_time_diff')

        # Comportamento da taxa de transferência entre tempos de buffering consecutivos
        buff_time_diff['F'] = fuzz.trapmf(buff_time_diff.universe, [-T, -T, (-2*T/3), 0])
        buff_time_diff['S'] = fuzz.trapmf(buff_time_diff.universe, [(-2*T/3), 0, 0, 5*T])
        buff_time_diff['R'] = fuzz.trapmf(buff_time_diff.universe, [0, 4*T, 5*T, 5*T])
        return buff_time_diff

    def set_quality_diff_membership(self):
        # Fator de qualidade varia de 0 a 2, com precisão de 0.01
        quality_diff = ctrl.Consequent(np.arange(0, 2, 0.01), 'quality_diff')
        N2 = 0.25   # Reduzir - R
        N1 = 0.5    # Reduzir pouco - SR
        Z = 1       # Não alterar - NC
        P1 = 1.5    # Aumentar pouco - SI
        P2 = 2      # Aumentar - I

        # Fator de incremento/decremento da qualidade do próximo segmento
        quality_diff['R'] = fuzz.trapmf(quality_diff.universe, [0, 0, N2, N1])
        quality_diff['SR'] = fuzz.trapmf(quality_diff.universe, [N2, N1, N1, Z])
        quality_diff['NC'] = fuzz.trapmf(quality_diff.universe, [N1, Z, Z, P1])
        quality_diff['SI'] = fuzz.trapmf(quality_diff.universe, [Z, P1, P1, P2])
        quality_diff['I'] = fuzz.trapmf(quality_diff.universe, [P1, P2, 2, 2])

        return quality_diff

    def get_controller_rules(self):
        rule2 = ctrl.Rule(self.buff_time['C'] & self.buff_time_diff['F'], self.quality_diff['SR'])
        rule1 = ctrl.Rule(self.buff_time['S'] & self.buff_time_diff['F'], self.quality_diff['R'])
        rule3 = ctrl.Rule(self.buff_time['L'] & self.buff_time_diff['F'], self.quality_diff['NC'])

        rule5 = ctrl.Rule(self.buff_time['C'] & self.buff_time_diff['S'], self.quality_diff['NC'])
        rule6 = ctrl.Rule(self.buff_time['L'] & self.buff_time_diff['S'], self.quality_diff['SI'])
        rule4 = ctrl.Rule(self.buff_time['S'] & self.buff_time_diff['S'], self.quality_diff['SR'])

        rule7 = ctrl.Rule(self.buff_time['S'] & self.buff_time_diff['R'], self.quality_diff['NC'])
        rule8 = ctrl.Rule(self.buff_time['C'] & self.buff_time_diff['R'], self.quality_diff['SI'])
        rule9 = ctrl.Rule(self.buff_time['L'] & self.buff_time_diff['R'], self.quality_diff['I'])

        return [rule1, rule2, rule3, rule4, rule5, rule6, rule7, rule8, rule9]
