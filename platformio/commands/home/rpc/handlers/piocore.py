# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import json
import os
import sys
from io import BytesIO, StringIO

import jsonrpc  # pylint: disable=import-error
from twisted.internet import threads  # pylint: disable=import-error

from platformio import __main__, __version__, util
from platformio.compat import PY2, is_bytes, string_types

try:
    from thread import get_ident as thread_get_ident
except ImportError:
    from threading import get_ident as thread_get_ident


class MultiThreadingStdStream(object):

    def __init__(self, parent_stream):
        self._buffers = {thread_get_ident(): parent_stream}

    def __getattr__(self, name):
        thread_id = thread_get_ident()
        if thread_id not in self._buffers:
            raise AttributeError(name)
        return getattr(self._buffers[thread_id], name)

    def write(self, value):
        thread_id = thread_get_ident()
        if thread_id not in self._buffers:
            self._buffers[thread_id] = BytesIO() if PY2 else StringIO()
        return self._buffers[thread_id].write(
            value.decode() if is_bytes(value) else value)

    def get_value_and_reset(self):
        result = ""
        try:
            result = self.getvalue()
            self.truncate(0)
            self.seek(0)
        except AttributeError:
            pass
        return result


class PIOCoreRPC(object):

    @staticmethod
    def setup_multithreading_std_streams():
        if isinstance(sys.stdout, MultiThreadingStdStream):
            return
        PIOCoreRPC.thread_stdout = MultiThreadingStdStream(sys.stdout)
        PIOCoreRPC.thread_stderr = MultiThreadingStdStream(sys.stderr)
        sys.stdout = PIOCoreRPC.thread_stdout
        sys.stderr = PIOCoreRPC.thread_stderr

    @staticmethod
    def call(args, options=None):
        PIOCoreRPC.setup_multithreading_std_streams()
        try:
            args = [
                str(arg) if not isinstance(arg, string_types) else arg
                for arg in args
            ]
        except UnicodeError:
            raise jsonrpc.exceptions.JSONRPCDispatchException(
                code=4002, message="PIO Core: non-ASCII chars in arguments")

        def _call_inline():
            with util.cd((options or {}).get("cwd") or os.getcwd()):
                exit_code = __main__.main(["-c"] + args)
            return (PIOCoreRPC.thread_stdout.get_value_and_reset(),
                    PIOCoreRPC.thread_stderr.get_value_and_reset(), exit_code)

        d = threads.deferToThread(_call_inline)
        d.addCallback(PIOCoreRPC._call_callback, "--json-output" in args)
        d.addErrback(PIOCoreRPC._call_errback)
        return d

    @staticmethod
    def _call_callback(result, json_output=False):
        out, err, code = result
        text = ("%s\n\n%s" % (out, err)).strip()
        if code != 0:
            raise Exception(text)
        return json.loads(out) if json_output else text

    @staticmethod
    def _call_errback(failure):
        raise jsonrpc.exceptions.JSONRPCDispatchException(
            code=4003,
            message="PIO Core Call Error",
            data=failure.getErrorMessage())

    @staticmethod
    def version():
        return __version__
