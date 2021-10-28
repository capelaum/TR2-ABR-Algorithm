# -*- coding: utf-8 -*-
"""
Grupo 9

@author: Felipe Oliveira Magno Neves    - 16/0016296
@author: Luís Vinicius Capelletto       - 16/0134544
@author: Matheus Augusto Silva Pinho    - 18/0024906

@description: FDASH algorithm: a Fuzzy-Based MPEG/DASH Adaption Algorithm
"""
import time
import numpy as np
import skfuzzy as fuzz
from r2a.ir2a import IR2A
from player.parser import *
from statistics import mean
from skfuzzy import control as ctrl


class R2A_FDASH(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        self.qi = []
        self.throughputs = []
        self.request_time = 0
        self.current_qi_index = 0
        self.smooth_troughput = None

        # Tamanhos de buffer
        self.buff_size_danger = 15
        self.buff_max = self.whiteboard.get_max_buffer_size()

        # Conjunto de regras
        self.rules = []
        # Tempo de estimativa do throughput da conexão
        self.d = 5
        # Tempo de buffering Alvo
        self.T = 35
        # Distancia do tempo de buffering atual para o alvo
        self.set_buffering_time_membership()
        # Diferença entre os ultimos 2 tempos de buffering
        self.set_buffering_time_diff_membership()
        # Diferença entre qualidades
        self.set_quality_diff_membership()
        # Configura controlador FLC
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
        self.pbs = self.whiteboard.get_playback_buffer_size()
        self.pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()

        if(len(self.pbt) > 1):
            self.update_troughputs()
            avg_throughput = mean(t[0] for t in self.throughputs)

            # Smooth trhoughput
            if self.smooth_troughput is None:
                self.smooth_troughput = avg_throughput
            self.smooth_troughput = 0.2 * self.smooth_troughput + 0.8 * avg_throughput

            # Entrada: Tempo de buffering atual
            self.FDASH.input['buff_time'] = self.pbt[-1]
            # Entrada: Diferença entre os 2 ultimos tempos de buffering
            self.FDASH.input['buff_time_diff'] = self.pbt[-1] - self.pbt[-2]
            self.FDASH.compute()
            # Armazena o fator de saída calculado pelo simulador FLC
            factor = self.FDASH.output['quality_diff']
            # Media dos k ultimos throughtputs multiplicada por fator
            desired_quality_id = self.smooth_troughput * factor
            desired_quality_id = self.minimize_switch_rate(desired_quality_id)

            self.print_request_info(msg, avg_throughput, factor, desired_quality_id)

            # Descobrir indice da maior qualidade mais proximo da qualidade desejada
            self.current_qi_index = self.get_selected_qi(desired_quality_id, True)

        # Nos primeiros segmentos, escolher a menor qualidade possível
        msg.add_quality_id(self.qi[self.current_qi_index])
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        t = time.perf_counter() - self.request_time
        throughput_tuple = (msg.get_bit_length() / t, time.perf_counter())
        self.throughputs.append(throughput_tuple)
        self.send_up(msg)

    def update_troughputs(self):
        current_time = time.perf_counter()
        while (current_time - self.throughputs[0][1] > self.d):
            self.throughputs.pop(0)

    def minimize_switch_rate(self, desired_quality_id):
        selected_qi = self.get_selected_qi(desired_quality_id)
        prev_quality_id = self.qi[self.current_qi_index]
        current_buff_size = self.pbs[-1][1]
        prev_buff_size = self.pbs[-2][1]
        predicted_buff = current_buff_size + (self.smooth_troughput / selected_qi) - 1

        if selected_qi > prev_quality_id and prev_buff_size <= self.buff_size_danger:
            return prev_quality_id
        if selected_qi < prev_quality_id and predicted_buff >= 0.5 * self.buff_max:
            return prev_quality_id

        return desired_quality_id

    def get_selected_qi(self, desired_quality_id, get_index=False):
        selected_qi_index = np.searchsorted(self.qi, desired_quality_id, side='right') - 1
        if get_index:
            return selected_qi_index if selected_qi_index > 0 else 0

        return self.qi[selected_qi_index] if selected_qi_index > 0 else self.qi[0]

    def set_buffering_time_membership(self):
        T = self.T
        buff_time = ctrl.Antecedent(np.arange(0, 5*T+0.01, 0.01), 'buff_time')

        # Diferença entre tempo de buffering atual com um valor alvo T
        buff_time['S'] = fuzz.trapmf(buff_time.universe, [0, 0, (2*T/3), T])
        buff_time['C'] = fuzz.trimf(buff_time.universe, [(2*T/3), T, 4*T])
        buff_time['L'] = fuzz.trapmf(buff_time.universe, [T, 4*T, np.inf,np.inf])
        self.buff_time = buff_time

    def set_buffering_time_diff_membership(self):
        T = self.T
        buff_time_diff = ctrl.Antecedent(np.arange(-T, 5*T+0.01, 0.01), 'buff_time_diff')

        # Diferencial dos ultimos 2 tempos de buffering
        buff_time_diff['F'] = fuzz.trapmf(buff_time_diff.universe, [-T, -T, (-2*T/3), 0])
        buff_time_diff['S'] = fuzz.trimf(buff_time_diff.universe, [(-2*T/3), 0, 4*T])
        buff_time_diff['R'] = fuzz.trapmf(buff_time_diff.universe, [0, 4*T, np.inf,np.inf])
        self.buff_time_diff = buff_time_diff

    def set_quality_diff_membership(self):
        N2 = 0.25
        N1 = 0.5
        Z = 1
        P1 = 1.5
        P2 = 2

        # Fator de qualidade varia de 0 a 2.5
        quality_diff = ctrl.Consequent(np.arange(0, P2+0.5, 0.01), 'quality_diff')

        # Fator de incremento/decremento da qualidade do próximo segmento
        quality_diff['R'] = fuzz.trapmf(quality_diff.universe, [0, 0, N2, N1])
        quality_diff['SR'] = fuzz.trimf(quality_diff.universe, [N2, N1, Z])
        quality_diff['NC'] = fuzz.trimf(quality_diff.universe, [N1, Z, P1])
        quality_diff['SI'] = fuzz.trimf(quality_diff.universe, [Z, P1, P2])
        quality_diff['I'] = fuzz.trapmf(quality_diff.universe, [P1, P2, np.inf,np.inf])
        self.quality_diff = quality_diff

    def set_rule(self, rule, output):
        rule = ctrl.Rule(rule, output)
        self.rules.append(rule)

    def set_controller_rules(self):
        self.set_rule(self.buff_time['S'] & self.buff_time_diff['F'], self.quality_diff['R'])
        self.set_rule(self.buff_time['C'] & self.buff_time_diff['F'], self.quality_diff['SR'])
        self.set_rule(self.buff_time['L'] & self.buff_time_diff['F'], self.quality_diff['NC'])

        self.set_rule(self.buff_time['S'] & self.buff_time_diff['S'], self.quality_diff['SR'])
        self.set_rule(self.buff_time['C'] & self.buff_time_diff['S'], self.quality_diff['NC'])
        self.set_rule(self.buff_time['L'] & self.buff_time_diff['S'], self.quality_diff['SI'])

        self.set_rule(self.buff_time['S'] & self.buff_time_diff['R'], self.quality_diff['NC'])
        self.set_rule(self.buff_time['C'] & self.buff_time_diff['R'], self.quality_diff['SI'])
        self.set_rule(self.buff_time['L'] & self.buff_time_diff['R'], self.quality_diff['I'])

    def print_request_info(self, msg, avg_throughput, factor, desired_quality_id):
        print("-----------------------------------------")
        buffering_time = self.pbt[-1]
        buffering_time_diff = buffering_time - self.pbt[-2]

        print("AVG Throughput = ", avg_throughput)
        print("buffering_time = ", buffering_time)
        print("buffering_time_diff = ", buffering_time_diff)
        print(">>>>> Fator de acréscimo/decréscimo =", factor)

        current_quality_id = self.qi[self.current_qi_index]
        print(f"CURRENT QUALITY ID: {current_quality_id}bps")
        print(f"DESIRED QUALITY ID: {int(desired_quality_id)}bps")

        playback_pauses = self.whiteboard.get_playback_pauses()
        print("PAUSES:", len(playback_pauses))
        print("SEGMENT ID:", msg.get_segment_id())
        print("-----------------------------------------")

    def print_throughputs(self):
        print("-----------------------------------------")
        print(f"THROUGHPUTS: {self.throughputs} >>>> LEN: {len(self.throughputs)}")
        if len(self.throughputs) >= 1:
            print(f"AVG THROUGHPUT: {int(mean(t[0] for t in self.throughputs))} Mbps")
        print("-----------------------------------------")

    def print_buffer_times(self):
        print("-----------------------------------------")
        print(f"BUFFER TIMES: {self.pbt} >>>> LEN: {len(self.pbt)}")
        if len(self.pbt) >= 1:
            print(f"AVG BUFFER TIME: {int(mean(self.pbt))}s")
        print("-----------------------------------------")

    def print_buffer_sizes(self):
        pbs = self.whiteboard.get_playback_buffer_size()
        print("-----------------------------------------")
        print(f"BUFFER SIZES: {pbs} >>>> LEN: {len(pbs)}")
        if len(pbs) >= 1:
            print(f"AVG BUFFER SIZE: {int(mean(b[1] for b in pbs))}")
        print("-----------------------------------------")

    def initialize(self):
        pass

    def finalization(self):
        pass