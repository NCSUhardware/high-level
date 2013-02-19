#!/usr/bin/python

# Major library imports
from numpy import array, sort, pi, cos, sin, zeros, arctan2
from numpy.random import random

# Enthought library imports
from enable.api import Component, ComponentEditor
from traits.api import HasTraits, Instance, Property, Int, Array, Range, cached_property
from traitsui.api import Item, View, Group

# Chaco imports
from chaco.api import ArrayDataSource, MultiArrayDataSource, DataRange1D, \
        LinearMapper, QuiverPlot, Plot, ArrayPlotData

# for sense 
from raycast import wall
from numpy.linalg import norm
from probability import gaussian 
from random import gauss  

from robot import *

size = (100, 100)

class Particles(object):

  @property
  def v(self):
    return array( zip(cos(self.theta), sin(self.theta)) )


  def __init__(self, robot, map, n = 100):
    # need to move the logic out of the GUI class and into its own particles class
    self.numpts = n
    self.map = map
    self.sensed = zeros((robot.num_sensors,self.numpts))  # modeled sensor data
    self.robot = robot

    # Create starting points for the vectors.
    self.x = sort(random(self.numpts)) * map.xdim  # sorted for axis tick calculation?
    self.y = random(self.numpts) * map.ydim
    self.theta = random(self.numpts)*2*pi

    self.prob = zeros(self.numpts)    # probability measurement, "weight"

  def __str__(self):
    out = "Particles:\n"
    for i in range(self.numpts):
      out += " %3d : (%5.2f , %5.2f)  @ %+0.2f\n" % (i, self.x[i], self.y[i], self.theta[i])
    return out

  # update particles based on movement model, predicting new pose
  #   ? how do we handle moving off map?  or into any wall?  just let resampling handle it?
  def move(self, dtheta, forward):
    x = self.x
    y = self.y
    theta = self.theta
    for i in range(self.numpts):
      theta[i] = (theta[i] + dtheta) % (2*pi)
      theta[i] = gauss(theta[i], dtheta * self.robot.noise_turn)
      dx = forward * cos(theta[i])
      dy = forward * sin(theta[i])
      x[i] = gauss(x[i] + dx, dx * self.robot.noise_move)
      y[i] = gauss(y[i] + dy, dy * self.robot.noise_move)

    # quick and dirty, keep things in range
    self.y = y.clip(0,self.map.ydim)
    self.x = x.clip(0,self.map.xdim)

    self.theta = theta
   
  # TODO: actual sensing logic should be a function in the sensor class, called from here
  def sense(self, rel_theta, map):
    x = self.x
    y = self.y
    data = zeros(self.numpts)
    sense_theta = [ ((t + rel_theta) % (2*pi)) for t in self.theta]
    for i in range(self.numpts):
      # the logic here is currently a duplicate of that for robot.sense(), but in 
      #   reality, robot.sense() will pull actual sensor data from the sensors (not a model)
      wx,wy = wall(x[i],y[i],sense_theta[i],map)
      # add gaussian noise to (otherwise exact) distance calculation?
      if wx == -1:  # no wall seen
        data[i] = norm( [map.xdim, map.ydim] )  # TODO should be sensor max reading
      else:
        data[i] = norm( [x[i]-wx, y[i]-wy] )
    return data

  def sense_all(self, map):
    x = self.x
    y = self.y
    for s in self.robot.sensors:
      # get the full set of particle senses for this sensor
      self.sensed[s.index] = self.sense(s.angle, map)
    #print "Particle sense:"
    #for i in range(self.numpts):
    #  print "  %0.2f, %0.2f @ %0.2f = " % (x[i], y[i], self.theta[i]),
    #  print ", ".join( [ "%s: %0.2f" % (s.name, self.sensed[s.index,i]) for s in self.robot.sensors ])

  # Currently assumes self.sensed and measured have both been updated
  def resample(self, measured):
    x = self.x
    y = self.y
    theta = self.theta
    #print "Updating particle weights:"
    # create an array of particle weights to use as resampling probability
    for i in range(self.numpts):
      self.prob[i] = 1.0
      for s in self.robot.sensors:
        # compare measured input of sensor versus the particle's value, adjusting prob accordingly
        #   TODO: refine 1.0 noise parameter to something meaningful
        self.prob[i] *= gaussian(self.sensed[s.index][i], 1.5, measured[s.index])
      #print "  %d : %0.2f, %0.2f @ %0.2f = %0.2e" % (i, x[i], y[i], theta[i], self.prob[i]) 

    # resample (x, y, theta) using wheel resampler
    new = []
    step = max(self.prob) * 2.0
    cur = int(random() * self.numpts)
    beta = 0.0
    for i in range(self.numpts):
      beta += random() * step  # 0 - step size
      while beta > self.prob[cur]:
        beta -= self.prob[cur]
        cur = (cur+1) % self.numpts
        #print "b: %0.2f, cur: %d, prob = %0.2f" % (beta, cur, self.prob[cur])
      new.append((cur, x[cur], y[cur], theta[cur], self.prob[cur]))
    #print "Resampled:"
    #for i in range(self.numpts):
    #  print " %d : %0.2f, %0.2f @ %+0.2f = %0.2e " % new[i]
    self.x = array([e[1] for e in new])
    self.y = array([e[2] for e in new])
    self.theta = array([e[3] for e in new])

  def guess(self):
    x = self.x.mean()
    y = self.y.mean()
    # average the vector components of theta individually to avoid jump between 0 and 2pi
    vx,vy = self.v.mean(axis=0)
    theta = arctan2(vy,vx) % (2*pi)
    return (x,y,theta)


class ParticlePlotter(HasTraits):

    qplot = Instance(QuiverPlot)
    #vsize = Range(low = -20.0, high = 20.0, value = 1.0)
    vsize = Int(10)
 
    # field dimensions, should we just hook in the map object and use it's values directly?
    xsize = Int
    ysize = Int

    # the associated robot object which our particles are simulating
    #   provides: x,y range and sensors list
    robot = Instance(Robot)
    particles = Instance(Particles)
   
    xs = ArrayDataSource()
    ys = ArrayDataSource()
    vector_ds = MultiArrayDataSource()

    # this defines the default view when configure_traits is called
    traits_view = View(Item('qplot', editor=ComponentEditor(size=size), show_label=False),
                       Item('vsize'), 
                       resizable=True)

    def do_redraw(self):
      self.xs.set_data(self.particles.x, sort_order='ascending')
      self.ys.set_data(self.particles.y)
      self.vector_ds.set_data(self.vectors)

    # magic function called when vsize trait is changed
    def _vsize_changed(self):
      #print "vsize is: ", self.vsize
      #self.vectors = array( zip(self.vsize*cos(self.theta), self.vsize*sin(self.theta)) )
      self.vector_ds.set_data(self.vectors)
      #print self.vector_ds.get_shape()
      self.qplot.request_redraw()
        
    #@cached_property
    @property
    def vectors(self):
      return self.particles.v * self.vsize

    # ?? dynamic instantiation of qplot verus setting up in init?
    #  robot param is the robot we are modeling -- source of params
    def _qplot_default(self):

      # Create an array data sources to plot all vectors at once
      #xs = ArrayDataSource(self.particles.x, sort_order='ascending')
      #ys = ArrayDataSource(self.particles.y)
      self.xs.set_data(self.particles.x, sort_order='ascending')
      self.ys.set_data(self.particles.y)

      #self.vector_ds = MultiArrayDataSource(self.vectors)
      self.vector_ds.set_data(self.vectors)

      # Set up the Plot
      xrange = DataRange1D()
      xrange.add(self.xs)
      xrange.set_bounds(0,self.xsize)
      yrange = DataRange1D()
      yrange.add(self.ys)
      yrange.set_bounds(0,self.ysize)
      qplot = QuiverPlot(index = self.xs, value = self.ys,
                      vectors = self.vector_ds,
                      #data_type = 'radial',  # not implemented
                      index_mapper = LinearMapper(range=xrange),
                      value_mapper = LinearMapper(range=yrange),
                      bgcolor = "white", line_color = "grey")

      qplot.aspect_ratio = float(self.xsize) / float(self.ysize)
      return qplot

if __name__ == "__main__":
    ParticlePlotter().configure_traits()