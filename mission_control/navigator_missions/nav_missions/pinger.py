#!/usr/bin/env python
from __future__ import division
import txros
import std_srvs.srv
import numpy as np
from navigator_msgs.srv import FindPinger, FindPingerRequest, SetFrequency, SetFrequencyRequest
from geometry_msgs.msg import Point
from std_srvs.srv import SetBool, SetBoolRequest
import navigator_tools
from visualization_msgs.msg import Marker, MarkerArray
from navigator_tools import fprint

___author___ = "Kevin Allen"

class PingerMission:
    OBSERVE_DISTANCE_METERS = 12
    GATE_CROSS_METERS = 10
    FREQ = 35000
    LISTEN_TIME = 15

    def __init__(self, navigator):
        self.navigator = navigator
        self.listen_client = self.navigator.nh.get_service_client('/hydrophones/set_listen', SetBool)
        self.pinger_client = self.navigator.nh.get_service_client('/hydrophones/find_pinger', FindPinger)
        self.reset_client = self.navigator.nh.get_service_client('/hydrophones/set_freq', SetFrequency)
        self.negate = False
        self.marker_pub = self.navigator.nh.advertise("/pinger/debug_marker",MarkerArray)
        self.markers = MarkerArray()
        self.last_id = 0;

    def reset_freq(self):
        return self.reset_client(SetFrequencyRequest(frequency=self.FREQ))

    def start_listen(self):
        return self.listen_client(SetBoolRequest(data=True))

    def stop_listen(self):
        return self.listen_client(SetBoolRequest(data=False))

    @txros.util.cancellableInlineCallbacks
    def get_objects(self):
        """Get position of 3 gates from database"""
        gate_1 = yield self.navigator.database_query("Gate_1")
        assert gate_1.found, "Gate 1 Not found"
        gate_1_pos = navigator_tools.rosmsg_to_numpy(gate_1.objects[0].position)[:2]

        gate_2 = yield self.navigator.database_query("Gate_2")
        assert gate_2.found, "Gate 2 Not found"
        gate_2_pos = navigator_tools.rosmsg_to_numpy(gate_2.objects[0].position)[:2]

        gate_3 = yield self.navigator.database_query("Gate_3")
        assert gate_3.found, "Gate 3 Not found"
        gate_3_pos = navigator_tools.rosmsg_to_numpy(gate_3.objects[0].position)[:2]

        self.gate_poses = np.array([gate_1_pos, gate_2_pos, gate_3_pos])

    @txros.util.cancellableInlineCallbacks
    def get_observation_poses(self):
        """Set 2 points to observe the pinger from, in front of gates 1 and 3"""
        self.get_gate_perp()
        #Make sure they are actually in a line
        if np.isnan(self.g_perp[0]) or np.isnan(self.g_perp[1]):
            raise Exception("Gates are not in a line")
        pose = (yield self.navigator.tx_pose)[0][:2]
        distance_test = np.array([np.linalg.norm(pose - (self.gate_poses[0] + self.OBSERVE_DISTANCE_METERS * self.g_perp)),
                                  np.linalg.norm(pose - (self.gate_poses[0] - self.OBSERVE_DISTANCE_METERS * self.g_perp))])
        if np.argmin(distance_test) == 1:
            self.negate = True
        if self.negate:
            self.observation_points = (np.append((self.gate_poses[0] - self.OBSERVE_DISTANCE_METERS * self.g_perp), 0),
                                       np.append((self.gate_poses[2] - self.OBSERVE_DISTANCE_METERS * self.g_perp), 0))
        else:
            self.observation_points = (np.append((self.gate_poses[0] + self.OBSERVE_DISTANCE_METERS * self.g_perp), 0),
                                       np.append((self.gate_poses[2] + self.OBSERVE_DISTANCE_METERS * self.g_perp), 0))
        self.look_at_points = (np.append(self.gate_poses[0], 0), np.append(self.gate_poses[2], 0))

    @txros.util.cancellableInlineCallbacks
    def search_samples(self):
        """Move to each observation point and listen to the pinger while sitting still"""
        yield self.get_observation_poses()
        for i,p in enumerate(self.observation_points):
            yield self.stop_listen()
            yield self.navigator.move.set_position(p).look_at(self.look_at_points[i]).go()
            yield self.start_listen()
            fprint("PINGER: Listening To Pinger at point {}".format(p), msg_color='green')
            yield self.navigator.nh.sleep(self.LISTEN_TIME)
        yield self.stop_listen()

    @txros.util.cancellableInlineCallbacks
    def get_pinger_pose(self):
        """Query pinger perception for the location of the pinger after observing"""
        res = yield self.pinger_client(FindPingerRequest())
        pinger_pose = res.pinger_position
        self.pinger_pose = navigator_tools.rosmsg_to_numpy(pinger_pose)[:2]
        self.distances = np.array([np.linalg.norm(self.pinger_pose - self.gate_poses[0]),
                                   np.linalg.norm(self.pinger_pose - self.gate_poses[1]),
                                   np.linalg.norm(self.pinger_pose - self.gate_poses[2])])

    @txros.util.cancellableInlineCallbacks
    def go_thru_gate(self):
        """Move to the points needed to go through the correct gate"""
        self.gate_index = np.argmin(self.distances)
        self.get_gate_thru_points()
        for p in self.gate_thru_points:
            yield self.navigator.move.set_position(p).go(initial_plan_time=5)

    @txros.util.cancellableInlineCallbacks
    def new_marker(self, ns="/debug", frame="enu", type = Marker.CUBE , position=(0,0,0), orientation=(0,0,0,1), color=(1,0,0)):
        marker = Marker()
        marker.ns = ns
        marker.header.stamp = yield self.navigator.nh.get_time()
        marker.header.frame_id = frame
        marker.type = type
        marker.action = marker.ADD
        marker.scale.x = 1.0
        marker.scale.y = 1.0
        marker.scale.z = 1.0
        marker.color.r = color[0]
        marker.color.g = color[1]
        marker.color.b = color[2]
        marker.color.a = 1.0
        marker.id = self.last_id
        marker.pose.orientation.x = orientation[0]
        marker.pose.orientation.y = orientation[1]
        marker.pose.orientation.z = orientation[2]
        marker.pose.orientation.w = orientation[3]
        marker.pose.position.x = position[0]
        marker.pose.position.y = position[1]
        marker.pose.position.z = position[2]
        self.last_id += 1
        self.markers.markers.append(marker)

    @txros.util.cancellableInlineCallbacks
    def get_colored_buoys(self):
        estimated_3_endbuoy = self.gate_poses[2] + (self.g_line * 5.0)
        estimated_1_endbuoy = self.gate_poses[0] - (self.g_line * 5.0)
        if self.negate:
            estimated_circle_buoy = self.gate_poses[1] + (self.g_perp * 30.0) #should check on correct side (negate bool)
        else:
            estimated_circle_buoy = self.gate_poses[1] - (self.g_perp * 30.0) #should check on correct side (negate bool)            

        yield self.new_marker(position=np.append(estimated_3_endbuoy,0), color=(1,0,0))
        yield self.new_marker(position=np.append(estimated_circle_buoy,0))
        yield self.new_marker(position=np.append(estimated_1_endbuoy,0), color=(0,1,0))
        yield self.marker_pub.publish(self.markers)

        totems = yield self.navigator.database_query("totem")
        if not totems.found:
            fprint("PINGER: not totems found", msg_color='red')
            return
        else:
            sorted_1 = sorted(totems.objects, key=lambda t: abs(np.linalg.norm(estimated_1_endbuoy - navigator_tools.rosmsg_to_numpy( t.position)[:2])))
            sorted_3 = sorted(totems.objects, key=lambda t: abs(np.linalg.norm(estimated_3_endbuoy - navigator_tools.rosmsg_to_numpy(t.position)[:2])))
            sorted_circle = sorted(totems.objects, key=lambda t: abs(np.linalg.norm(estimated_circle_buoy - navigator_tools.rosmsg_to_numpy(t.position)[:2])))
            fprint("PINGER: gate 3 color {}".format(sorted_3[0].color), msg_color='blue')
            fprint("PINGER: gate 1 color {}".format(sorted_1[0].color), msg_color='blue')

            yield self.new_marker(position=navigator_tools.rosmsg_to_numpy(sorted_circle[0].position), color=(0,0,1))
            yield self.new_marker(position=navigator_tools.rosmsg_to_numpy(sorted_1[0].position), color=(0,0,1))
            yield self.new_marker(position=navigator_tools.rosmsg_to_numpy(sorted_3[0].position), color=(0,0,1))
        yield self.marker_pub.publish(self.markers)


    @txros.util.cancellableInlineCallbacks
    def set_active_pinger(self):
        """Set the paramter for the active pinger identified for use in other mission"""
        yield self.navigator.mission_params["acoustic_pinger_active_index"].set(int(self.gate_index)+1)
        yield self.get_colored_buoys()

    @txros.util.cancellableInlineCallbacks
    def circle_buoy(self):
         pass

    def get_gate_perp(self):
        """Calculate a perpendicular to the line formed by the three gates"""
        delta_g = self.gate_poses[2] - self.gate_poses[0]
        rot_right = np.array([[0, -1], [1, 0]])
        g_perp = rot_right.dot(delta_g)
        g_perp = g_perp /  np.linalg.norm(g_perp)
        self.g_perp = g_perp
        self.g_line = delta_g / np.linalg.norm(delta_g)

    def get_gate_thru_points(self):
        """Set points needed to cross through correct gate"""
        if self.negate:
            pose1 = self.gate_poses[self.gate_index] - self.GATE_CROSS_METERS * self.g_perp
            pose2 = self.gate_poses[self.gate_index] + self.GATE_CROSS_METERS * self.g_perp
        else:
            pose1 = self.gate_poses[self.gate_index] + self.GATE_CROSS_METERS * self.g_perp
            pose2 = self.gate_poses[self.gate_index] - self.GATE_CROSS_METERS * self.g_perp
        self.gate_thru_points = (np.append(pose1, 0), np.append(pose2, 0))

    @txros.util.cancellableInlineCallbacks
    def run(self):
        fprint("PINGER: Starting Pinger Mission", msg_color='green')
        fprint("PINGER: Reseting Frequency", msg_color='green')
        yield self.reset_freq()
        fprint("PINGER: Getting database objects", msg_color='green')
        yield self.get_objects()
        fprint("PINGER: Calculating observation points", msg_color='green') 
        yield self.get_observation_poses()
        fprint("PINGER: Searching each sample", msg_color='green') 
        yield self.search_samples()
        fprint("PINGER: Getting pinger position", msg_color='green') 
        yield self.get_pinger_pose()
        fprint("PINGER: Going through gate", msg_color='green') 
        yield self.go_thru_gate()
        fprint("PINGER: Setting active pinger parameter", msg_color='green') 
        yield self.set_active_pinger()
        #  yield self.circle_buoy()
        fprint("PINGER: Ended Pinger Mission", msg_color='green') 

@txros.util.cancellableInlineCallbacks
def main(navigator, **kwargs):
    mission = PingerMission(navigator)
    yield mission.run()
