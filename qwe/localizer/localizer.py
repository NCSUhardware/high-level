#!/usr/bin/python

from numpy import random, pi, zeros

import robot, particles, map, pose
import time  # sleep

import std_sensors, std_noise

import sys
sys.path.append('..')
import mapping.map_class
sys.modules['map_class'] = mapping.map_class  # deal with the fact we pickled a module in another dir
import mapping.pickler

# blocks status + map = walls 
def run( start_x, start_y, start_theta, ipc_channel = None, shared_data = {}, map_data = None ):

  start_pose = pose.Pose(start_x,start_y,start_theta)
  ideal = robot.SimRobot(start_pose, std_sensors.default)

  if not ipc_channel:
    print "Using stub IPC"
    ipc_channel = Fake_IPC(start_pose, map_data, delay = 1.0)

  print "Start: ", start_pose
  print

  localizer = DumbLocalizer(start_pose)
  #localizer = particles.ParticleLocalizer(std_sensors.default, std_noise.noise_params, map_data, pcount=1000)

  while True:
    msg = ipc_channel.read()
    turn, move = msg['turn'], msg['move']
    print "Message: Turn: %+0.2f, Move: %0.2f" % (turn, move)
    ideal.move(turn, move)
    localizer.move(turn, move)
    print "Ideal: ", ideal.pose
    localizer.update(msg['sensors'])
    guess = localizer.guess()
    print "Guess: ", guess
    print
    shared_data['x'] = guess.x
    shared_data['y'] = guess.y
    shared_data['theta'] = guess.theta

#################################
class Fake_IPC(object):
  def __init__(self, start_pose, map_data, delay = 0.0):
    self.delay = delay
    self.map = map_data
    self.simbot = robot.SimRobot(pose = start_pose, sensors = std_sensors.default, 
                            noise_params = std_noise.noise_params)

  def read(self):
    print "Robot: ", self.simbot.pose
    turn = random.random() * pi - pi/2
    move = random.random() 
    self.simbot.move(turn,move)
    measured = self.simbot.sense(self.map)
    # %todo: x, y, theta -> dx, dy, dtheta
    msg = {'turn': turn, 'move': move, 'sensors': measured}
    time.sleep(self.delay)
    return msg

#################################

class DumbLocalizer(object):
  def __init__(self, start_pose):
    self.r = robot.SimRobot(start_pose)
  def move(self, turn, move):
    self.r.move(turn, move)
  def update(self, sensors):
    pass
  def guess(self):
    return self.r.pose

#################################

if __name__ == '__main__':

  #m = map.Map('maps/test3.map')
  map_obj = mapping.pickler.unpickle_map('../mapping/map.pkl')
  m = map.Map.from_map_class(map_obj)

  start_x = m.xdim / 2
  start_y = m.ydim / 2
  start_theta = 0

  run(start_x, start_y, start_theta, map_data = m)

