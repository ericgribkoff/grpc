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


import logging
import os
import threading

_LOGGER = logging.getLogger(__name__)

_AWAIT_THREADS_TIMEOUT_SECONDS = 5

_TRUE_VALUES = ['y', 'yes', 't', 'true', 'on', '1']

# This flag enables experimental support within gRPC Python for applications
# that will fork() without exec(). When enabled, gRPC Python will attempt to
# halt all of its internally created threads before the fork syscall proceeds.
#
# For this to be successful, the application must not have any ongoing RPCs, and
# any callbacks from gRPC Python into user code must not block and must execute 
# quickly (they will be from a gRPC-created thread that must terminate before 
# the fork syscall proceeds).
#
# Similarly, the application should not have multiple threads of its own running
# when fork is invoked. This means that fork must not be invoked in response to
# a gRPC Python initiated callback for an asynchronous RPC, as this means that
# at least two threads are active: the application'ss main thread and the thread
# executing the callback.
#
# Channel connectivity subscriptions will be unsubscribed when forking, as gRPC
# Python must shut down the thread it uses to poll for channel updates.
#
# The gRPC C++ core library requires additional changes to support fork that are
# in progress. Until this work is complete, combining gRPC Python and fork may
# still result in failed RPCs due to shared connections between the child and
# parent process, even with the GRPC_PYTHON_EXPERIMENTAL_FORK_SUPPORT flag
# enabled.
#
# This flag is not supported on Windows.
_EXPERIMENTAL_FORK_SUPPORT_ENABLED = (
    os.environ.get('GRPC_PYTHON_EXPERIMENTAL_FORK_SUPPORT', '0')
        .lower() in _TRUE_VALUES)

cdef void __prefork() nogil:
    with gil:
        with _fork_state.fork_in_progress_condition:
            _fork_state.fork_in_progress = True
        if not _fork_state.active_thread_count.await_zero_threads(
                _AWAIT_THREADS_TIMEOUT_SECONDS):
            _LOGGER.exception(
                'Failed to shutdown gRPC Python threads prior to fork. '
                'Behavior after fork will be undefined.')


cdef void __postfork_parent() nogil:
    with gil:
        with _fork_state.fork_in_progress_condition:
            _fork_state.post_fork_child_cleanup_callbacks = []
            _fork_state.fork_in_progress = False
            _fork_state.fork_in_progress_condition.notify_all()


cdef void __postfork_child() nogil:
    with gil:
        with _fork_state.fork_in_progress_condition:
            for state_to_reset in _fork_state.postfork_states_to_reset:
                state_to_reset.reset_postfork_child()
            _fork_state.post_fork_child_cleanup_callbacks = []
            _fork_state.fork_in_progress = False


def fork_handlers_and_grpc_init():
    grpc_init()
    if _EXPERIMENTAL_FORK_SUPPORT_ENABLED:
        with _fork_state.fork_handler_registered_lock:
            if not _fork_state.fork_handler_registered:
                pthread_atfork(&__prefork, &__postfork_parent, &__postfork_child)
                _fork_state.fork_handler_registered = True


def fork_managed_thread(target, args=()):
    if _EXPERIMENTAL_FORK_SUPPORT_ENABLED:
        def managed_target(*args):
            _fork_state.active_thread_count.increment()
            target(*args)
            _fork_state.active_thread_count.decrement()
        return threading.Thread(target=managed_target, args=args)
    else:
        return threading.Thread(target=target, args=args)


def block_if_fork_in_progress(postfork_state_to_reset=None):
    with _fork_state.fork_in_progress_condition:
        # print("block_if_fork_in_progress")
        if not _fork_state.fork_in_progress:
            # print("not blocking")
            return
        if postfork_state_to_reset is not None:
            _fork_state.postfork_states_to_reset.append(postfork_state_to_reset)
        _fork_state.active_thread_count.decrement()
        print("blocking")
        _fork_state.fork_in_progress_condition.wait()
        print("done blocking")
        _fork_state.active_thread_count.increment()


class _ActiveThreadCount(object):
    def __init__(self):
        self._num_active_threads = 0
        self._condition = threading.Condition()

    def increment(self):
        with self._condition:
            self._num_active_threads += 1

    def decrement(self):
        with self._condition:
            self._num_active_threads -= 1
            if self._num_active_threads == 0:
                self._condition.notify_all()

    def await_zero_threads(self, timeout_secs):
        with self._condition:
            if self._num_active_threads > 0:
                self._condition.wait(timeout_secs)
            return self._num_active_threads == 0


class _ForkState(object):
    def __init__(self):
        self.fork_in_progress_condition = threading.Condition()
        self.fork_in_progress = False
        self.postfork_states_to_reset = []
        self.fork_handler_registered_lock = threading.Lock()
        self.fork_handler_registered = False
        self.active_thread_count = _ActiveThreadCount()


_fork_state = _ForkState()
