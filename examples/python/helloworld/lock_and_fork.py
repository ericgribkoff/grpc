"""TODO(ericgribkoff): DO NOT SUBMIT without one-line documentation for lock_and_fork.

TODO(ericgribkoff): DO NOT SUBMIT without a detailed description of lock_and_fork.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import threading
import time

rl = threading.Lock()

def a():
  rl.acquire()
  time.sleep(10)

def b():
  print(rl.acquire(False))

def main():
  print("Thread: ", threading.current_thread())
  t = threading.Thread(target=a)
  t.start()
  print(rl)
  if os.fork() > 0:
    print("Thread: ", threading.current_thread())
    print(rl)
    b()
    time.sleep(1)
    print(rl)
    b()


if __name__ == '__main__':
  main()
