#!/usr/bin/python

from numpy import array, zeros
import csv

# loads map into 2d list:
#   [y][x] are map coordinates
#   [0][0] is bottom left corner
class Map():
  def __init__(self, filename = None, scale = 1, logger = None):
    self.logger = logger
    if filename:
      data = list( csv.reader(open(filename, 'r')))
      data = [ [int(x) for x in y] for y in data  ]  # convert string to ints
      data.reverse()
      self.data = array(data)
      self.scale = scale  # inches per element
      self.map_obj = None
      self.logger.debug("Map initialized from file: %s" % filename)
      self.logger.debug("Map dimensions %s" % self)

  def xy(self):
    """ Converts from matrix of 0s and 1s to an array of xy pairs.
        New coordinates are offset by 0.5 to represent center of wall (for plotting) """
    xy = []
    for y in range(len(self.data)):
      for x in range(len(self.data[0])):
        if self.data[y][x] == 1:
          xy.append([x+0.5,y+0.5])
    return array(xy)

  @classmethod
  def from_map_class(self, map_obj, logger = None):
    logger.debug("Map initialized from map_class object")
    m = Map(logger = logger)
    m.map_obj = map_obj
    m.update()
    #desc_to_walls = zeros(10,dtype=bool)
    #desc_to_walls[8] = True

    return m

  def update(self):
    self.logger.debug("Repopulating map data from class")
    desc_to_walls = zeros(10,dtype=int)
    desc_to_walls[8] = 1
    self.data = array([desc_to_walls[i] for i in self.map_obj.grid[:][:]['desc']])
    self.scale = 1.0 / self.map_obj.scale
    self.logger.debug("Map dimensions %s" % self)

  @property
  def xdim(self):
    return len(self.data[0])

  @property
  def ydim(self):
    return len(self.data)

  @property
  def x_inches(self):
    return len(self.data[0]) * self.scale

  @property
  def y_inches(self):
    return len(self.data) * self.scale

  def __str__(self):
    return "Map: (%d, %d) = (%0.2f, %0.2f) inches" % (self.xdim, self.ydim, self.xdim * self.scale, self.ydim * self.scale)

