import os
import sys
import random
from contextlib import contextmanager
from tempfile import mkdtemp, gettempdir
import shutil
import string
import bisect
from functools import lru_cache

def median(l):
    """ Returns the median value of a list.
        It uses the quickselect algorithm, which runs in O(n) average
        time, but may use up to O(n^2) for malformed data (e.g., if the
        elements are not distinct).
    """
    if len(l) == 0:
        raise RuntimeError("can't get the median of an array with no values.")
    n = len(l)/2
    while True:
        pivot = random.choice(l)
        count = 0
        left, right = [], []
        for elem in l:
            if elem < pivot:
                left.append(elem)
            elif elem > pivot:
                right.append(elem)
            else:
                count += 1
        if n < len(left):
            l = left
        elif n < len(left) + count:
            return pivot
        else:
            l = right
            n -= len(left) + count

def infinity():
    """ returns the ieee float infinity falue """
    return float('Inf')

def memoize(func):
    """ memoizing decorator """
    def cache_value(arg):
        if arg in cache_value.cache:
            return cache_value.cache[arg]
        else:
            result = func(arg)
            cache_value.cache[arg] = result
            return result
    cache_value.cache = {}
    return cache_value

def mkdir_p(path):
    """ Create a directory and all its parent directories.
        Does not return an error if any of the directories (including the
        top level directory) already exists. """
    paths = []
    while len(path)>1:
        paths.append(path)
        path = os.path.dirname(path)

    for path in reversed(paths):
        try:
            os.mkdir(path)
        except OSError as e:
            if e.errno != 17:
                raise

def change_extension(filename, new_extension):
    """ Given a filename, change its extension.
        E.g. change_extension("test.xml", "html") returns "test.html" 
    """
    return os.path.splitext(filename)[0]+"."+new_extension

def random_string(length):
    """ return a random of the given length, consisting of random 
        uppercase ascii and digit characters """
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(length))

def tempnam():
    """ returns a temporary file name. 
        Warning: named temporary files are insecure on many systems. """
    return os.path.join(gettempdir(), "file"+random_string(8))

@contextmanager
def tempfile(suffix="tmp"):
    """ context manager for creating & deleting a temporary file.
        Use 'with tempfile() as ...' for creating a file
        for a temporary operation and deleting it afterwards """
    file = tempnam() + "." + suffix
    try:
        yield file
    finally:
        try:
            os.unlink(file)
        except OSError:
            pass

@contextmanager
def tempdir(prefix):
    """ context manager for creating & deleting a temporary directory.
        Use 'with tempdir() as ...' for creating a directory
        for a temporary operation and deleting it afterwards """
    dir = mkdtemp(prefix)
    try:
        yield dir
    finally:
        shutil.rmtree(dir)

def invert_dict(d):
    """ Invert a dictionary. For example, {'a': 3, 'b': 4} will get
        converted to {3: 'a', 4: 'b'} """
    n = {}
    for k,v in d.items():
        n[v] = k
    return n

def group_by(array, f=id):
    """ Iterator. Given an array, combine consecutive identical values into
        groups, that are returned individually (as arrays). 
        For example, [3,3,3,4,4,1,4] returns [3,3,3],[4,4,4],[1],[4].
    """
    if len(array) == 0:
        return
    group = [array[0]]
    last_style = f(array[0])
    for e in array[1:]:
        style = f(e)
        if style != last_style:
            yield group
            group = [e]
            last_style = style
        else:
            group.append(e)
    yield group

def shorten_warnings():
    """ Configure the warning module to output more dense warnings that
        (usually) fit into a single line """
    import warnings
    def shorter_warning(message, category, filename, lineno, file=None, line=None):
        return '%s:%s: %s\n' % (os.path.basename(filename).encode('utf8'), lineno, message)
    warnings.formatwarning = shorter_warning

class NumericRange:
    """ parses ranges like "1,2-5,7-" """
    def __init__(self, s):
        """ Parse a range given as a string and create a new NumericRange
            object that can be used to query existence of individual integers."""
        r = []
        for item in s.split(","):
            if "-" in item:
                # range, e.g. "1-5", or "1-"
                numbers = item.split("-")
                r.append((int(numbers[0]), "l"))
                if numbers[1] == "":
                    r.append((sys.maxsize, "r"))
                else:
                    r.append((int(numbers[1]), "r"))
            else:
                # single page, e.g. "3"
                r += [(int(item), "l"), (int(item), "r")]

        r.sort()

        # merge overlapping ranges
        depth = 0
        self.ranges = []
        for page,t in r:
            if t == "l":
                if depth == 0:
                    self.ranges.append((page,t))
                depth += 1
            elif t == "r":
                depth -= 1
                if depth == 0:
                    self.ranges.append((page,t))

    def contains(self, nr):
        """ returns true if this NumericRange contains the given integer """
        r = bisect.bisect_left(self.ranges, (nr,"\0"), 0, len(self.ranges))
        if r >= len(self.ranges):
            return False
        if nr >= self.ranges[r][0] and self.ranges[r][1] == "l":
            return True
        if nr <= self.ranges[r][0] and self.ranges[r][1] == "r":
            return True
        return False

@lru_cache()
def parse_range(range_string):
    """ Parses ranges like "1,2-5,7-" and returns an object
        that has the 'contains' function """
    if isinstance(range_string, NumericRange):
        return range_string
    return NumericRange(range_string)

def is_in_range(nr, range_string):
    """ Returns true if nr is in the range given by a string
        is_in_range(3,"1-5") -> true
        is_in_range(6,"1-5") -> false
        is_in_range(4,"1-3,5-7") -> false
    """
    return parse_range(range_string).contains(nr)


