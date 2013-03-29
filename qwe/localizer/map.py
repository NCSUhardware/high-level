#!/usr/bin/python

from numpy import array
import csv

# loads map into 2d list:
#   [y][x] are map coordinates
#   [0][0] is bottom left corner
class Map():
  def __init__(self, filename, scale = 1):
    data = list( csv.reader(open(filename, 'r')))
    data = [ [int(x) for x in y] for y in data  ]  # convert string to ints
    data.reverse()
    self.data = data
    self.scale = scale  # inches per element

  def xy(self):
    """ Converts from matrix of 0s and 1s to an array of xy pairs.
        New coordinates are offset by 0.5 to represent center of wall (for plotting) """
    xy = []
    for y in range(len(self.data)):
      for x in range(len(self.data[0])):
        if self.data[y][x] == 1:
          xy.append([x+0.5,y+0.5])
    return array(xy)

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

