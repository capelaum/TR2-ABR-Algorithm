from r2a.ir2a import IR2A
from player.parser import *
import time
from statistics import mean

class R2A_FDASH(IR2A):
    def __init__(self, id):
        IR2A.__init__(self, id)
        # guarda qualidades disponíveis
        self.qi = []
        self.currentQi = 0

    def handle_xml_request(self, msg):
        self.request_time = time.perf_counter()
        self.send_down(msg)

    def handle_xml_response(self, msg):
        parsed_mpd = parse_mpd(msg.get_payload())
        self.qi = parsed_mpd.get_qi()
        self.send_up(msg)

    def handle_segment_size_request(self, msg):
        bufferTimeHistogram = self.whiteboard.get_playback_segment_size_time_at_buffer()

        if(bufferTimeHistogram.len > 1):
            bufferingTime = bufferTimeHistogram[-1]
            differentialBufferingTime = self.lastBufferingTime[-1] - bufferTimeHistogram[-2]
            # setar self.currentQi usando FDASH e variáveis calculadas

        msg.add_quality_id(self.qi[self.currentQi])
        self.send_down(msg)

    def handle_segment_size_response(self, msg):
        self.send_up(msg)

    def initialize(self):
        pass

    def finalization(self):
        pass
