from r2a.ir2a import IR2A
from player.parser import *
import time
from statistics import mean


class R2A_FDASH(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        # guarda qualidades disponíveis
        self.qi = []
        self.current_qi = 0

    def handle_xml_request(self, msg):
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_xml_response(self, msg):
        parsed_mpd = parse_mpd(msg.get_payload())
        self.qi = parsed_mpd.get_qi()
        self.send_up(msg)

    def handle_segment_size_request(self, msg):
        buffer_time_histogram = self.whiteboard.get_playback_segment_size_time_at_buffer()

        if(len(buffer_time_histogram) > 1):
            buffering_time = buffer_time_histogram[-1]
            buffering_time_diff = buffering_time - buffer_time_histogram[-2]
            # setar self.current_qi usando FDASH e variáveis calculadas

        msg.add_quality_id(self.qi[self.current_qi])
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        self.send_up(msg)

    def initialize(self):
        pass

    def finalization(self):
        pass
