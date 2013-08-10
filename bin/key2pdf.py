#!/usr/bin/python
import os
import sys
binary_path = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(binary_path, ".."))

from optparse import OptionParser
import keynote.utils
from keynote import keynote

def parse_options(*args):
    parser = OptionParser()
    parser.add_option("-o", "--output", dest="output", default="output.pdf",
                      action="store", help="Output file")
    parser.add_option("-p", "--pages", dest="pages", default="1-",
                      action="store", help="Pages to convert")
    opts,files = parser.parse_args(*args)
    if len(files) == 0:
        raise RuntimeError("missing file argument")
    if len(files) > 1:
        raise RuntimeError("can only convert one file at a time")
    return opts,files[0]

if __name__ == "__main__":
    keynote.utils.shorten_warnings()
    opts,filename = parse_options()

    keynote.set_options(opts)

    key = keynote.Keynote(filename)
    key.save(opts.output)
