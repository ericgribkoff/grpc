# Copyright 2018 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
import threading
import time #TODO remove
import traceback

cdef void __prefork() nogil:
  with gil:
#    def waitAndUnlock():
#      time.sleep(3)
#      print('waking up and finishing!')
#      thread_barrier.decrement_threads()
#    t = threading.Thread(target=waitAndUnlock)
#    thread_barrier.increment_threads()
#    t.start()
#    print('num_threads went to 0')
    traceback.print_stack()
    print('invoked?')
    print('awaiting tb.num_threads=0')
    print('tb.num_threads=0? ', thread_count.await_zero_threads(5))

fork_handler_lock = threading.Lock()
fork_handlers_registered = False

def fork_handlers_and_grpc_init():
  grpc_init()
  global fork_handler_lock
  global fork_handlers_registered
  fork_handler_lock.acquire()
  if not fork_handlers_registered:
    print('Installing fork handler', pthread_atfork(&__prefork,
         NULL, NULL))
    fork_handlers_registered = True
  fork_handler_lock.release()

class ThreadCount(object):
  def __init__(self):
    self.num_threads = 0
    self.lock = threading.Condition()
    self.forking = False
    self.forking_lock = threading.RLock()

  def increment(self):
    print('incrementing thread count')
    traceback.print_stack()
    self.lock.acquire()
    self.num_threads += 1
    self.lock.release()

  def decrement(self):
    print('decrementing thread count')
    traceback.print_stack()
    self.lock.acquire()
    if self.num_threads > 0:
      self.num_threads -= 1
    # TODO else error
    if self.num_threads == 0:
      self.lock.notify_all()
    self.lock.release()

  def set_forking(val):
    with self.forking_lock:
      forking = val

  def await_zero_threads(self, timeout_secs):
    with self.lock:
      if self.num_threads > 0:
        self.lock.wait(timeout_secs)
      return self.num_threads == 0

thread_count = ThreadCount()
