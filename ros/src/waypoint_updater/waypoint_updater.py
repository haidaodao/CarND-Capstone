#!/usr/bin/env python

import rospy
from geometry_msgs.msg import PoseStamped
from styx_msgs.msg import Lane, Waypoint
import tf
import math
import threading
import time

'''
This node will publish waypoints from the car's current position to some `x` distance ahead.

As mentioned in the doc, you should ideally first implement a version which does not care
about traffic lights or obstacles.

Once you have created dbw_node, you will update this node to use the status of traffic lights too.

Please note that our simulator also provides the exact location of traffic lights and their
current status in `/vehicle/traffic_lights` message. You can use this message to build this node
as well as to verify your TL classifier.
'''

LOOKAHEAD_WPS = 200  # Number of waypoints we will publish. You can change this number


def universal2car_ref(x, y, car_x, car_y, car_yaw):
    """
    Translates a 2D Cartesian coordinate from the universal reference system to the car reference system, where the car
    is at coordinates (0, 0) and with yaw 0 (the x-axis is oriented with the car).
    :param x: the x coordinate, in the universal reference system.
    :param y: the y coordinate, in the universal reference system.
    :param car_x: the car x coordinate.
    :param car_y:  the car y coordinate.
    :param car_yaw: the car yaw, in radians.
    :return: a pair of coordinates in the car reference system
    """
    shift_x = x - car_x
    shift_y = y - car_y
    x_res = (shift_x * math.cos(-car_yaw) - shift_y * math.sin(-car_yaw))
    y_res = (shift_x * math.sin(-car_yaw) + shift_y * math.cos(-car_yaw))
    return x_res, y_res


def car2universal_ref(x, y, car_x, car_y, car_yaw):
    """
    Translates a 2D Cartesian coordinate from the car reference system to the universal reference system.
    :param x: the x coordinate, in the car reference system.
    :param y: the y coordinate, in the car reference system.
    :param car_x: the car x coordinate.
    :param car_y: the car y coordinate.
    :param car_yaw: the car yaw.
    :return: a pair of coordinates in the universal reference system
    """
    unrotated_x = x * math.cos(car_yaw) - y * math.sin(car_yaw)
    unrotated_y = x * math.sin(car_yaw) + y * math.cos(car_yaw)
    x_res = unrotated_x + car_x;
    y_res = unrotated_y + car_y;
    return x_res, y_res


def unpack_pose(pose):
    """
    Extracts the three Cartesian coordinates from a geometry_msgs/Pose.
    :param pose: the given pose.
    :return: a tuple with the x, y and z coordinates extracted from the pose.
    """
    x = pose.position.x
    y = pose.position.y
    z = pose.position.z
    return x, y, z


def poses_distance(pose1, pose2):
    """
    Computes the Euclidean distance between two poses.
    :param pose1: the first given pose.
    :param pose2: the second given pose.
    :return: the distance between poses.
    """
    x1, y1, z1 = unpack_pose(pose1)
    x2, y2, z2 = unpack_pose(pose2)
    distance = ((x1-x2)**2+(y1-y2)**2+(z1-z2)**2)**.5
    return distance


def get_bearing_from_pose(my_pose, from_pose):
    """
    Computes the bearing of a second pose, as seen from a first pose.
    :param my_pose: the pose from which the bearing is observed.
    :param from_pose: the pose whose bearing is taken.
    :return: the computed bearing, in radians.
    """
    my_x, my_y, _ = unpack_pose(my_pose)
    from_x, from_y, _ = unpack_pose(from_pose)
    bearing = math.atan2(from_y-my_y, from_x-my_x)
    return bearing


def get_next_waypoint_idx(pose, waypoints, starting_idx):
    """
    Determines the index of the first waypoint in front of the car in a list of waypoints, starting from a given
    index. Note that the car is assumed to moved toward waypoints of increasing index (not allowed to go backward, or
    the other way around).
    :param pose: the car pose, inclusive of orientation.
    :param waypoints: the list of waypoints.
    :param starting_idx: starting position in parameter `waypoints` from which the index is searched.
    :return: the found index.
    """
    # Get the car yaw, x and y coordinates (in the universal reference system)
    quaternion = (pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w)
    _, _, pose_yaw = tf.transformations.euler_from_quaternion(quaternion)
    pose_x, pose_y, _ = unpack_pose(pose)

    ''' Starting from the waypoint with index `starting_idx` in `waypoints`, find the first one with a positive
    x coordinate in the car reference system. That is the first waypoint in front of the car. '''
    wp_i = starting_idx
    direction = 1  # Currently always set to 1. Setting it to -1 would allow to search backward in the waypoints list.
    while True:
        # Fetch the waypoint coordinates, in the universal reference system.
        wp_x, wp_y, _ = unpack_pose(waypoints[wp_i].pose.pose)
        # Convert them to the car reference system
        wp_x_car_ref, _ = universal2car_ref(x=wp_x, y=wp_y, car_x=pose_x, car_y=pose_y, car_yaw=pose_yaw)
        # If the x coordinate is positive, then the waypoint is in front of the car, what we are looking for.
        if wp_x_car_ref >= 0:
            break
        # Otherwise check the next waypoint in the list; take care to wrap around when reaching the end of the list.
        wp_i = (wp_i + direction) % len(waypoints)
        assert wp_i != starting_idx  # If it gets in an infinite loop, then it is a bug!

    return wp_i


class WaypointUpdater(object):
    def __init__(self):
        rospy.init_node('waypoint_updater', log_level=rospy.DEBUG)

        ''' Note that subscription to /current_pose is done inside self.waypoints_cb(). No point in receiving pose
        updates before having received the track waypoints. '''
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        # TODO: Add a subscriber for /traffic_waypoint and /obstacle_waypoint below

        self.final_waypoints_pub = rospy.Publisher('final_waypoints', Lane, queue_size=1)

        # TODO: Add other member variables you need below
        self.waypoints = None

        ''' Every time a /current_pose message is received, method pose_cb() determines the first track waypoint
        in front of the car. To do so, it scans the list of waypoints in self.waypoints. Once it finds the
        wanted waypoint, it saves its index in self.prev_wp_idx (here below).
        Next time a /current_pose event is received, method pose_cb() will start searching self.waypoints from
        the position saved in self.prev_we_idx; this is more efficient than looking every time starting from
        the beginning of the list. '''
        self.prev_wp_idx = 0

        self.received_pose_count = 0  # Counts how many car pose updates (/current_pose messages) have been received
        self.previous_pose_cb_time = None  # Time of when the last pose (/current_pose messages) has been received
        self.total_time = .0  # Total time spent in executing pose_cb(), for performance monitoring

        rospy.spin()

    def pose_cb(self, msg):  # This is being called at 50 Hz
        """
        Processes pose update messages. The current implementation takes a list of LOOKAHEAD_WPS waypoints,
        out of the track waypoints, that are in front of the car, set them at a constant speed, and publishes them for
        the waypoint_follower to follow. The car will try to follow those waypoints at constant speed (ignoring traffic
        lights). TODO update to keep traffic ligths into consideration.
        :param msg: the received message.
        """
        # Update time and count information about received poses
        now = time.time()
        delta_t = 0 if self.previous_pose_cb_time is None else now-self.previous_pose_cb_time
        self.total_time += delta_t
        self.previous_pose_cb_time = now
        self.received_pose_count += 1 # TODO protect with mutex!

        rospy.logdebug('Processing pose #{}; delta_t from previous processing is {}s with average {}s'.format(self.received_pose_count,
                                                                                                              delta_t,
                                                                                                              self.total_time/self.received_pose_count))

        # Determine the index in `self.waypoints` of the first waypoint in front of the car
        pose_i = get_next_waypoint_idx(msg.pose, self.waypoints, self.prev_wp_idx)
        self.prev_wp_idx = pose_i

        # Collect LOOKAHEAD_WPS waypoints starting from that index, and publish them.
        direction = 1
        lane = Lane()
        lane.header.frame_id = '/world'
        lane.header.stamp = rospy.Time(0)
        for count in xrange(LOOKAHEAD_WPS):
            i = (pose_i+count*direction) % len(self.waypoints)
            wp =self.waypoints[i]
            wp.twist.twist.linear.x = 9.  # Currentlys setting the speed to this constant value (in m/s).
            lane.waypoints.append(wp)
        self.final_waypoints_pub.publish(lane)
        total_time = time.time() - now
        rospy.logdebug('Time spent in pose_cb: {}s'.format(total_time))

    def waypoints_cb(self, waypoints):
        """
        Receives the list of waypoints making the track and store it in self.waypoints
        :param waypoints: the received message
        """
        # This callback should be called only once, with the list of waypoints not yet initialised.
        assert self.waypoints is None

        self.waypoints= waypoints.waypoints  # No need to guarantee mutual exclusion in accessing this data member

        # Now that the waypoints describing the track have been received, it is time to subscribe to pose updates.
        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)

    def traffic_cb(self, msg):
        # TODO: Callback for /traffic_waypoint message. Implement
        pass

    def obstacle_cb(self, msg):
        # TODO: Callback for /obstacle_waypoint message. We will implement it later
        pass

    def get_waypoint_velocity(self, waypoint):
        return waypoint.twist.twist.linear.x

    def set_waypoint_velocity(self, waypoints, waypoint, velocity):
        waypoints[waypoint].twist.twist.linear.x = velocity

    def distance(self, waypoints, wp1, wp2):
        dist = 0
        dl = lambda a, b: math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2  + (a.z-b.z)**2)
        for i in range(wp1, wp2+1):
            dist += dl(waypoints[wp1].pose.pose.position, waypoints[i].pose.pose.position)
            wp1 = i
        return dist


if __name__ == '__main__':
    try:
        WaypointUpdater()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start waypoint updater node.')