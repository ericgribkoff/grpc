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


import threading
import traceback # TODO remove


cdef void __prefork() nogil:
    with gil:
        with _fork_state.fork_in_progress_lock:
            _fork_state.fork_in_progress = True
        traceback.print_stack()
        print('awaiting tb.num_threads=0')
        # TODO - replace 5 with constant
        print('tb.num_threads=0? ', _fork_state.thread_count.await_zero_threads(5))


cdef void __postfork() nogil:
    with gil:
        with _fork_state.fork_in_progress_lock:
            _fork_state.fork_in_progress = False


def fork_handlers_and_grpc_init():
    grpc_init()
    with _fork_state.fork_handler_registered_lock:
        if not _fork_state.fork_handler_registered:
            print('Installing fork handler after grpc_init',
                pthread_atfork(&__prefork,
                &__postfork, &__postfork))
            _fork_state.fork_handler_registered = True


def fork_managed_thread(target, args=()):
    def managed_target(*args):
        _fork_state.thread_count.increment()
        target(*args)
        _fork_state.thread_count.decrement()
    return threading.Thread(target=managed_target, args=args)


def is_fork_in_progress():
    with _fork_state.fork_in_progress_lock:
        return _fork_state.fork_in_progress


class _ThreadCount(object):
    def __init__(self):
        self._num_threads = 0
        self._condition = threading.Condition()

    def increment(self):
        print('incrementing thread count')
        traceback.print_stack()
        with self._condition:
            self._num_threads += 1

    def decrement(self):
        print('decrementing thread count')
        traceback.print_stack()
        with self._condition:
            self._num_threads -= 1
            if self._num_threads == 0:
                self._condition.notify_all()

    def await_zero_threads(self, timeout_secs):
        with self._condition:
            if self._num_threads > 0:
                self._condition.wait(timeout_secs)
            return self._num_threads == 0


class _ForkState(object):
    def __init__(self):
        self.fork_in_progress_lock = threading.Lock()
        self.fork_in_progress = False
        self.fork_handler_registered_lock = threading.Lock()
        self.fork_handler_registered = False
        self.thread_count = _ThreadCount()


_fork_state = _ForkState()