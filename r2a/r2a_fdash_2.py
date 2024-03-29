# -*- coding: utf-8 -*-
"""
Grupo 9

@author: Felipe Oliveira Magno Neves    - 16/0016296
@author: Luís Vinicius Capelletto       - 16/0134544
@author: Matheus Augusto Silva Pinho    - 18/0024906

@description: FDASH Alternativo: Fuzzy-Based Quality Adaption Algorithm for improving QoE from
MPEG/DASH Video
"""
import time
import numpy as np
import skfuzzy as fuzz
from r2a.ir2a import IR2A
from player.parser import *
from statistics import mean
from skfuzzy import control as ctrl


class R2A_FDASH_2(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        self.qi = []
        self.throughputs = []
        self.request_time = 0
        self.current_qi_index = 0
        self.smooth_troughput = None
        self.rules = []
        self.d = 5
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
        self.pbs = self.whiteboard.get_playback_buffer_size()
        self.update_troughputs()
        avg_throughput = mean(t[0] for t in self.throughputs)

        if self.smooth_troughput is None:
            self.smooth_troughput = avg_throughput
        self.smooth_troughput = 0.2 * self.smooth_troughput + 0.8 * avg_throughput

        if len(self.pbs) > 1:
            self.FDASH.input['buff_size'] = self.pbs[-1][1]
            self.FDASH.input['buff_size_diff'] = self.pbs[-1][1] - self.pbs[-2][1]
            self.FDASH.input['rate'] = self.throughputs[-1][0] / self.qi[self.current_qi_index]
            self.FDASH.compute()
            factor = self.FDASH.output['factor']

            desired_quality_id = self.smooth_troughput * factor
            desired_quality_id = self.minimize_switch_rate(desired_quality_id)
            self.print_request_info(msg, avg_throughput, factor, desired_quality_id)

            # Descobrir indice de maior qualidade mais proximo da qualidade desejada
            self.current_qi_index = self.get_selected_qi(desired_quality_id, True)

        msg.add_quality_id(self.qi[self.current_qi_index])
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        t = time.perf_counter() - self.request_time
        self.throughputs.append((msg.get_bit_length() / t, time.perf_counter()))
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
        predicted_buff = current_buff_size + (self.smooth_troughput / selected_qi - 1)

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
        N2 = 0.25
        N1 = 0.5
        Z = 1
        P1 = 1.5
        P2 = 2
        factor = ctrl.Consequent(np.arange(0, 2.6, 0.1), 'factor')

        # Fator de incremento/decremento da qualidade do próximo segmento
        factor['R'] = fuzz.trapmf(factor.universe, [0, 0, N2, N1])
        factor['SR'] = fuzz.trimf(factor.universe, [N2, N1, Z])
        factor['NC'] = fuzz.trimf(factor.universe, [N1, Z, P1])
        factor['SI'] = fuzz.trimf(factor.universe, [Z, P1, P2])
        factor['I'] = fuzz.trapmf(factor.universe, [P1, P2, np.inf, np.inf])
        self.factor = factor

    def set_rule(self, rule, output):
        rule = ctrl.Rule(rule, output)
        self.rules.append(rule)

    def set_controller_rules(self):
        buff_size = self.buff_size
        buff_size_diff = self.buff_size_diff
        rate = self.rate
        factor = self.factor

        # Buffer Dangerous
        self.set_rule(buff_size['D'] & buff_size_diff['F'] & rate['L'], factor['R'])
        self.set_rule(buff_size['D'] & buff_size_diff['F'] & rate['S'], factor['R'])
        self.set_rule(buff_size['D'] & buff_size_diff['F'] & rate['H'], factor['R'])

        self.set_rule(buff_size['D'] & buff_size_diff['S'] & rate['L'], factor['R'])
        self.set_rule(buff_size['D'] & buff_size_diff['S'] & rate['S'], factor['SR'])
        self.set_rule(buff_size['D'] & buff_size_diff['S'] & rate['H'], factor['SR'])

        self.set_rule(buff_size['D'] & buff_size_diff['R'] & rate['L'], factor['R'])
        self.set_rule(buff_size['D'] & buff_size_diff['R'] & rate['S'], factor['SR'])
        self.set_rule(buff_size['D'] & buff_size_diff['R'] & rate['H'], factor['SR'])

        # Buffer Low
        self.set_rule(buff_size['L'] & buff_size_diff['F'] & rate['L'], factor['SR'])
        self.set_rule(buff_size['L'] & buff_size_diff['F'] & rate['S'], factor['NC'])
        self.set_rule(buff_size['L'] & buff_size_diff['F'] & rate['H'], factor['NC'])

        self.set_rule(buff_size['L'] & buff_size_diff['S'] & rate['L'], factor['NC'])
        self.set_rule(buff_size['L'] & buff_size_diff['S'] & rate['S'], factor['NC'])
        self.set_rule(buff_size['L'] & buff_size_diff['S'] & rate['H'], factor['NC'])

        self.set_rule(buff_size['L'] & buff_size_diff['R'] & rate['L'], factor['NC'])
        self.set_rule(buff_size['L'] & buff_size_diff['R'] & rate['S'], factor['NC'])
        self.set_rule(buff_size['L'] & buff_size_diff['R'] & rate['H'], factor['SI'])

        # Buffer Safe
        self.set_rule(buff_size['S'] & buff_size_diff['F'] & rate['L'], factor['SI'])
        self.set_rule(buff_size['S'] & buff_size_diff['F'] & rate['S'], factor['SI'])
        self.set_rule(buff_size['S'] & buff_size_diff['F'] & rate['H'], factor['I'])

        self.set_rule(buff_size['S'] & buff_size_diff['S'] & rate['L'], factor['SI'])
        self.set_rule(buff_size['S'] & buff_size_diff['S'] & rate['S'], factor['SI'])
        self.set_rule(buff_size['S'] & buff_size_diff['S'] & rate['H'], factor['I'])

        self.set_rule(buff_size['S'] & buff_size_diff['R'] & rate['L'], factor['SI'])
        self.set_rule(buff_size['S'] & buff_size_diff['R'] & rate['S'], factor['I'])
        self.set_rule(buff_size['S'] & buff_size_diff['R'] & rate['H'], factor['I'])

    def print_request_info(self, msg, avg_throughput, factor, desired_quality_id):
        print("-----------------------------------------")
        print("AVG Throughput =", avg_throughput)
        print("SMOOTH Throughput =", self.smooth_troughput)
        print("buffering_size =", self.pbs[-1][1])
        print("buffering_size_diff =", self.pbs[-1][1] - self.pbs[-2][1])
        print(">>>>> Fator de acréscimo/decréscimo =", factor)
        print(f"CURRENT QUALITY ID: {self.qi[self.current_qi_index]}bps")
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
        pbt = self.whiteboard.get_playback_segment_size_time_at_buffer()
        print("-----------------------------------------")
        print(f"BUFFER TIMES: {pbt} >>>> LEN: {len(pbt)}")
        if len(pbt) >= 1:
            print(f"AVG BUFFER TIME: {int(mean(pbt))}s")
        print("-----------------------------------------")

    def print_buffer_sizes(self):
        print("-----------------------------------------")
        print(f"BUFFER SIZES: {self.pbs} >>>> LEN: {len(self.pbs)}")
        if len(self.pbs) >= 1:
            print(f"AVG BUFFER SIZE: {int(mean(b[1] for b in self.pbs))}")
        print("-----------------------------------------")

    def initialize(self):
        pass

    def finalization(self):
        pass
