#!/usr/bin/env python

###
# Copyright (c) 2002, Jeremiah Fincher
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

"""
Module for some slight database-independence for simple databases.
"""

__revision__ = "$Id$"

import supybot.fix as fix

import csv
import math
import sets
import random

import supybot.cdb as cdb
import supybot.utils as utils

class Error(Exception):
    """General error for this module."""
    
class MappingInterface(object):
    """This is a class to represent the underlying representation of a map
    from integer keys to strings."""
    def __init__(self, filename, **kwargs):
        """Feel free to ignore the filename."""
        raise NotImplementedError

    def get(id):
        """Gets the record matching id.  Raises KeyError otherwise."""
        raise NotImplementedError

    def set(id, s):
        """Sets the record matching id to s."""
        raise NotImplementedError

    def add(self, s):
        """Adds a new record, returning a new id for it."""
        raise NotImplementedError

    def remove(self, id):
        "Returns and removes the record with the given id from the database."
        raise NotImplementedError

    def __iter__(self):
        "Return an iterator over (id, s) pairs.  Not required to be ordered."
        raise NotImplementedError

    def flush(self):
        """Flushes current state to disk."""
        raise NotImplementedError

    def close(self):
        """Flushes current state to disk and invalidates the Mapping."""
        raise NotImplementedError

    def vacuum(self):
        "Cleans up in the database, if possible.  Not required to do anything."
        pass

class FlatfileMapping(MappingInterface):
    def __init__(self, filename, maxSize=10**6):
        self.filename = filename
        try:
            fd = file(self.filename)
            strId = fd.readline().rstrip()
            self.maxSize = len(strId)
            try:
                self.currentId = int(strId)
            except ValueError:
                raise Error, 'Invalid file for FlatfileMapping: %s' % filename
        except EnvironmentError, e:
            # File couldn't be opened.
            self.maxSize = int(math.log10(maxSize))
            self.currentId = 0
            self._incrementCurrentId()

    def _canonicalId(self, id):
        if id is not None:
            return str(id).zfill(self.maxSize)
        else:
            return '-'*self.maxSize
    
    def _incrementCurrentId(self, fd=None):
        fdWasNone = fd is None
        if fdWasNone:
            fd = file(self.filename, 'a')
        fd.seek(0)
        self.currentId += 1
        fd.write(self._canonicalId(self.currentId))
        fd.write('\n')
        if fdWasNone:
            fd.close()
        
    def _splitLine(self, line):
        line = line.rstrip('\r\n')
        (id, s) = line.split(':', 1)
        return (id, s)

    def _joinLine(self, id, s):
        return '%s:%s\n' % (self._canonicalId(id), s)

    def add(self, s):
        line = self._joinLine(self.currentId, s)
        try:
            fd = file(self.filename, 'r+')
            fd.seek(0, 2) # End.
            fd.write(line)
            return self.currentId
        finally:
            self._incrementCurrentId(fd)
            fd.close()

    def get(self, id):
        strId = self._canonicalId(id)
        try:
            fd = file(self.filename)
            fd.readline() # First line, nextId.
            for line in fd:
                (lineId, s) = self._splitLine(line)
                if lineId == strId:
                    return s
            raise KeyError, id
        finally:
            fd.close()

    def set(self, id, s):
        strLine = self._joinLine(id, s)
        try:
            fd = file(self.filename, 'r+')
            self.remove(id, fd)
            fd.seek(0, 2) # End.
            fd.write(strLine)
        finally:
            fd.close()

    def remove(self, id, fd=None):
        fdWasNone = fd is None
        strId = self._canonicalId(id)
        try:
            if fdWasNone:
                fd = file(self.filename, 'r+')
            fd.seek(0)
            fd.readline() # First line, nextId
            pos = fd.tell()
            line = fd.readline()
            while line:
                (lineId, _) = self._splitLine(line)
                if lineId == strId:
                    fd.seek(pos)
                    fd.write(self._canonicalId(None))
                    fd.seek(pos)
                    fd.readline() # Same line we just rewrote the id for.
                pos = fd.tell()
                line = fd.readline()
            # We should be at the end.
        finally:
            if fdWasNone:
                fd.close()

    def __iter__(self):
        fd = file(self.filename)
        fd.readline() # First line, nextId.
        for line in fd:
            (id, s) = self._splitLine(line)
            if not id.startswith('-'):
                yield (int(id), s)
        fd.close()

    def vacuum(self):
        infd = file(self.filename)
        outfd = utils.transactionalFile(self.filename)
        outfd.write(infd.readline()) # First line, nextId.
        for line in infd:
            if not line.startswith('-'):
                outfd.write(line)
        infd.close()
        outfd.close()

    def flush(self):
        pass # No-op, we maintain no open files.

    def close(self):
        self.vacuum() # Should we do this?  It should be fine.
        

class CdbMapping(MappingInterface):
    def __init__(self, filename, **kwargs):
        self.filename = filename
        self.db = cdb.open(filename, 'c', **kwargs)
        if 'nextId' not in self.db:
            self.db['nextId'] = '1'

    def _getNextId(self):
        i = int(self.db['nextId'])
        self.db['nextId'] = str(i+1)
        return i

    def get(self, id):
        return self.db[str(id)]

    def set(self, id, s):
        self.db[str(id)] = s

    def add(self, s):
        id = self._getNextId()
        self.set(id, s)
        return id

    def remove(self, id):
        del self.db[str(id)]

    def __iter__(self):
        for (id, s) in self.db.iteritems():
            if id != 'nextId':
                yield (int(id), s)

    def flush(self):
        self.db.flush()

    def close(self):
        self.db.close()


class DB(object):
    Mapping = 'flat' # This is a good, sane default.
    Record = None
    def __init__(self, filename, Mapping=None, Record=None):
        if Record is not None:
            self.Record = Record
        if Mapping is not None:
            self.Mapping = Mapping
        if isinstance(self.Mapping, basestring):
            self.Mapping = Mappings[self.Mapping]
        self.map = self.Mapping(filename)

    def _newRecord(self, id, s):
        record = self.Record(id=id)
        record.deserialize(s)
        return record 

    def get(self, id):
        s = self.map.get(id)
        return self._newRecord(id, s)

    def set(self, id, record):
        s = record.serialize()
        self.map.set(id, s)

    def add(self, record):
        s = record.serialize()
        return self.map.add(s)

    def remove(self, id):
        self.map.remove(id)

    def __iter__(self):
        for (id, s) in self.map:
            # We don't need to yield the id because it's in the record.
            yield self._newRecord(id, s)

    def select(self, p):
        for record in self:
            if p(record):
                yield record

    def random(self):
        return random.choice(self)

    def flush(self):
        self.map.flush()

    def close(self):
        self.map.close()

Mappings = {
    'cdb': CdbMapping,
    'flat': FlatfileMapping,
    }


class Record(type):
    """__fields should be a list of two-tuples, (name, converter) or
    (name, (converter, default))."""
    def __new__(cls, clsname, bases, dict):
        defaults = {}
        converters = {}
        fields = []
        for name in dict['__fields__']:
            if isinstance(name, tuple):
                (name, spec) = name
            else:
                spec = utils.safeEval
            assert name != 'convert' and name != 'id'
            fields.append(name)
            if isinstance(spec, tuple):
                (converter, default) = spec
            else:
                converter = spec
                default = None
            defaults[name] = default
            converters[name] = converter
        del dict['__fields__']

        def __init__(self, id=None, convert=False, **kwargs):
            if id is not None:
                assert isinstance(id, int), 'id must be an integer.'
            self.id = id
            set = sets.Set()
            for (name, value) in kwargs.iteritems():
                assert name in fields, 'name must be a record value.'
                set.add(name)
                if convert:
                    setattr(self, name, converters[name](value))
                else:
                    setattr(self, name, value)
            for name in fields:
                if name not in set:
                    setattr(self, name, defaults[name])

        def serialize(self):
            return csv.join([repr(getattr(self, name)) for name in fields])

        def deserialize(self, s):
            unseenRecords = sets.Set(fields)
            for (name, strValue) in zip(fields, csv.split(s)):
                setattr(self, name, converters[name](strValue))
                unseenRecords.remove(name)
            for name in unseenRecords:
                setattr(self, record, defaults[record])
        
        dict['__init__'] = __init__
        dict['serialize'] = serialize
        dict['deserialize'] = deserialize
        return type.__new__(cls, clsname, bases, dict)
    
                
            

    
# vim:set shiftwidth=4 tabstop=8 expandtab textwidth=78:
