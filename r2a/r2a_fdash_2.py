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
        self.d = 5

        self.buff_sizes = [0]
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
        self.buff_sizes.append(self.whiteboard.get_amount_video_to_play())
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
            # self.print_buffer_sizes()

            # Descobrir indice da maior qualidade mais proximo da qualidade desejada
            selected_qi_index = np.searchsorted(self.qi, desired_quality_id, side='right') - 1
            self.current_qi_index = selected_qi_index if selected_qi_index > 0 else 0

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

    def get_selected_qi(self, desired_quality_id):
        selected_qi = self.qi[0]
        for quality_id in self.qi:
            if desired_quality_id >= quality_id:
                selected_qi = quality_id
            else:
                break
        return selected_qi

    def minimize_switch_rate(self, desired_quality_id):
        selected_qi = self.get_selected_qi(desired_quality_id)
        pred_buff_size = self.pbs[-1][1] + (self.smooth_troughput / selected_qi - 1)
        prev_quality_id = self.qi[self.current_qi_index]
        prev_buff_size = self.pbs[-2][1]

        if selected_qi > prev_quality_id and prev_buff_size <= self.buff_size_danger:
            return prev_quality_id

        # if selected_qi < prev_quality_id and pred_buff_size >= self.buff_max / 2:
        #     return prev_quality_id

        return desired_quality_id

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
        print(f"MY BUFFER SIZES: {self.buff_sizes} >>>> LEN: {len(self.buff_sizes)}")
        if len(self.buff_sizes) >= 1:
            print(f"MY AVG BUFFER SIZE: {int(mean(self.buff_sizes))}")
        print("-----------------------------------------")

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

    def set_controller_rules(self):
        buff_size = self.buff_size
        buff_size_diff = self.buff_size_diff
        rate = self.rate
        factor = self.factor

        # Buffer Dangerous
        rule1 = ctrl.Rule(buff_size['D'] & buff_size_diff['F'] & rate['L'], factor['R'])
        rule2 = ctrl.Rule(buff_size['D'] & buff_size_diff['F'] & rate['S'], factor['R'])
        rule3 = ctrl.Rule(buff_size['D'] & buff_size_diff['F'] & rate['H'], factor['R'])

        rule4 = ctrl.Rule(buff_size['D'] & buff_size_diff['S'] & rate['L'], factor['R'])
        rule5 = ctrl.Rule(buff_size['D'] & buff_size_diff['S'] & rate['S'], factor['SR'])
        rule6 = ctrl.Rule(buff_size['D'] & buff_size_diff['S'] & rate['H'], factor['SR'])

        rule7 = ctrl.Rule(buff_size['D'] & buff_size_diff['R'] & rate['L'], factor['R'])
        rule8 = ctrl.Rule(buff_size['D'] & buff_size_diff['R'] & rate['S'], factor['SR'])
        rule9 = ctrl.Rule(buff_size['D'] & buff_size_diff['R'] & rate['H'], factor['SR'])

        # Buffer Low
        rule10 = ctrl.Rule(buff_size['L'] & buff_size_diff['F'] & rate['L'], factor['SR'])
        rule11 = ctrl.Rule(buff_size['L'] & buff_size_diff['F'] & rate['S'], factor['NC'])
        rule12 = ctrl.Rule(buff_size['L'] & buff_size_diff['F'] & rate['H'], factor['NC'])

        rule13 = ctrl.Rule(buff_size['L'] & buff_size_diff['S'] & rate['L'], factor['NC'])
        rule14 = ctrl.Rule(buff_size['L'] & buff_size_diff['S'] & rate['S'], factor['NC'])
        rule15 = ctrl.Rule(buff_size['L'] & buff_size_diff['S'] & rate['H'], factor['NC'])

        rule16 = ctrl.Rule(buff_size['L'] & buff_size_diff['R'] & rate['L'], factor['NC'])
        rule17 = ctrl.Rule(buff_size['L'] & buff_size_diff['R'] & rate['S'], factor['NC'])
        rule18 = ctrl.Rule(buff_size['L'] & buff_size_diff['R'] & rate['H'], factor['SI'])

        # Buffer Safe
        rule19 = ctrl.Rule(buff_size['S'] & buff_size_diff['F'] & rate['L'], factor['SI'])
        rule20 = ctrl.Rule(buff_size['S'] & buff_size_diff['F'] & rate['S'], factor['SI'])
        rule21 = ctrl.Rule(buff_size['S'] & buff_size_diff['F'] & rate['H'], factor['I'])

        rule22 = ctrl.Rule(buff_size['S'] & buff_size_diff['S'] & rate['L'], factor['SI'])
        rule23 = ctrl.Rule(buff_size['S'] & buff_size_diff['S'] & rate['S'], factor['SI'])
        rule24 = ctrl.Rule(buff_size['S'] & buff_size_diff['S'] & rate['H'], factor['I'])

        rule25 = ctrl.Rule(buff_size['S'] & buff_size_diff['R'] & rate['L'], factor['SI'])
        rule26 = ctrl.Rule(buff_size['S'] & buff_size_diff['R'] & rate['S'], factor['I'])
        rule27 = ctrl.Rule(buff_size['S'] & buff_size_diff['R'] & rate['H'], factor['I'])

        self.rules = [
            rule1, rule2, rule3, rule4, rule5, rule6, rule7, rule8, rule9,
            rule10, rule11, rule12, rule13, rule14, rule15, rule16, rule17, rule18,
            rule19, rule20, rule21, rule22, rule23, rule24, rule25, rule26, rule27
        ]

    def initialize(self):
        pass

    def finalization(self):
        pass
