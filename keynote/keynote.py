import zipfile
from contextlib import contextmanager
import sys
import logging
from . import utils

try:
    import Image
except ImportError:
    # python 3 doesn't have this yet
    Image = None

from .xml import XML, ns
import cairo
import os
from io import StringIO
import numpy
from scipy import misc
from .fontface import find_cairo_font

class Options:
    settings = { 
        "pages": "1",
    }
    def __init__(self):
        pass
    def __getattr__(self, key):
        return Options.settings.get(key)

options = Options()

def set_options(o):
    for n in dir(o):
        Options.settings[n] = getattr(o,n)

info = logging.getLogger('keynote').info
warn = logging.getLogger('keynote').warn

class AssumptionError(Exception):
    pass

class Keynote(object):
    def __init__(self, filename):
        Keynote.current = self
        self.z = zipfile.ZipFile(filename)
        self.filenames = set(self.z.namelist())
        self.used_filenames = set()

        with self.open("index.apxl") as f:
            self.index = Index(self, f)

        self.slides = self.index.slides()

    @staticmethod
    def read_file(path):
        Keynote.current.used_filenames.add(path)
        try:
            fi = Keynote.current.z.open(path.encode("utf8"))
        except KeyError:
            # try a "shared" file
            try:
                fi = open(os.path.join(os.path.dirname(__file__), path), "rb")
            except IOError:
                warn("Couldn't find %s" % path)
                return None
            except UnicodeEncodeError:
                warn("Couldn't open %s" % path)
                return None
        data = fi.read()
        fi.close()
        return data

    @staticmethod
    @contextmanager
    def open(path):
        Keynote.current.used_filenames.add(path)
        fi = Keynote.current.z.open(path)
        try:
            yield fi
        finally:
            fi.close()

    def __contains__(self, path):
        return None if (path not in self.filenames) else True

    def save(self, output_file):
        import cairo
        surface = cairo.PDFSurface(output_file, self.index.width, self.index.height)
        context = cairo.Context(surface)
        info("Rendering...")
        for slide in self.slides:
            if utils.is_in_range(slide.nr, options.pages):
                slide.render(context)
        surface.finish()

class StrokeStyle(object):
    def __init__(self, color, width, cap_style, join_style, miter_limit):
        self.color = color
        self.width = width
        self.cap_style = cap_style
        self.join_style = join_style
        self.miter_limit = miter_limit

    join_map = {
        "miter": cairo.LINE_JOIN_MITER,
        "bevel": cairo.LINE_JOIN_BEVEL,
        "round": cairo.LINE_JOIN_ROUND,
    }
    cap_map = {
        "butt": cairo.LINE_CAP_BUTT,
        "round": cairo.LINE_CAP_ROUND,
        "square": cairo.LINE_CAP_SQUARE,
    }
    def apply(self, device):
        r,g,b,a = self.color
        device.set_source_rgba(r,g,b,a)
        device.set_line_width(self.width)
        device.set_line_join(self.join_map[self.join_style])
        device.set_line_cap(self.cap_map[self.cap_style])
        if self.miter_limit is not None:
            device.set_miter_limit(self.miter_limit)

class Primitives(object):

    @staticmethod
    def has_color(element):
        e = element.find(ns("sf:color"))
        return e is not None

    @staticmethod
    def parse_color(element):
        """
            <sf:color xsi:type="sfa:calibrated-white-color-type" sfa:w="1" sfa:a="1"/>

            OR

            <sf:color xsi:type="sfa:calibrated-rgb-color-type" 
                      sfa:r="0.89" sfa:g="0.89" sfa:b="0.89" 
                      sfa:a="1"/>

        """
        e = element.find(ns("sf:color"))
        if e is None:
            # TODO: does empty color mean "inherit" or "cancel inheritance, use default"?
            return None
        a = float(e.get(ns("sfa:a")))
        w = e.get(ns("sfa:w"))
        if w is not None:
            w = float(w)
            return (w,w,w,a)
        r = e.get(ns("sfa:r"))
        if r is not None:
            r = float(r)
            g = float(e.get(ns("sfa:g")))
            b = float(e.get(ns("sfa:b")))
            return (r,g,b,a)
        c = e.get(ns("sfa:c"))
        if c is not None:
            c = float(c)
            m = float(e.get(ns("sfa:m")))
            y = float(e.get(ns("sfa:y")))
            k = float(e.get(ns("sfa:k")))
            white = 1.0 - k
            r = white - (c * white);
            g = white - (m * white);
            b = white - (y * white);
            return (r,g,b,a)
        raise Exception("unknown color space")

    @staticmethod
    def parse_stroke_style(element):
        """ <sf:stroke>
              <sf:stroke sfa:ID="SFRStroke-152" sf:miter-limit="4" sf:width="9" sf:cap="butt" sf:join="miter">
                <sf:color xsi:type="sfa:calibrated-rgb-color-type" sfa:r="0.3" sfa:g="0.5" sfa:b="0.15" sfa:a="1"/>
                <sf:pattern sfa:ID="SFRStrokePattern-135" sf:phase="0" sf:type="solid">
                  <sf:pattern/>
                </sf:pattern>
              </sf:stroke>
            </sf:stroke>
        """
        stroke = element.find_or_lookup("sf:stroke") or element
        if stroke is None:
            warn("unknown stroke type %s" % element[0].shorttag)
            return None
        if not Primitives.has_color(stroke):
            warn("unknown stroke type %s" % element[0].shorttag)
            return None
        color = Primitives.parse_color(stroke)
        cap_style = stroke.get("sf:cap")
        join_style = stroke.get("sf:join")
        miter_limit = float(stroke.get("sf:miter-limit") or 0)
        width = float(stroke.get("sf:width") )
        if width == 0:
            return None
        return StrokeStyle(color, width, cap_style, join_style, miter_limit)

    @staticmethod
    def parse_number(element):
        e = element.find(ns("sf:number"))
        if e is None:
            # TODO: does empty number mean "inherit" or "cancel inheritance, use default"?
            return None
        if e.get(ns("sfa:type")) == "i":
            return int(e.get(ns("sfa:number")))
        else: # f
            return float(e.get(ns("sfa:number")))

    @staticmethod
    def parse_string(element):
        e = element.find(ns("sf:string"))
        if e is None:
            # TODO: does empty string mean "inherit" or "cancel inheritance, use default"?
            return None
        return e.get(ns("sfa:string"))

class Bitmap(object):
    filename_to_surface = {}
    """
        <x>
          <sf:unfiltered sfa:ID="SFRImageBinary-0">
            <sf:size sfa:w="400" sfa:h="400"/>
            <sf:data sfa:ID="SFEData-0" sf:path="Shared/tile_blackboard_blue.jpg" sf:displayname="theme-files/tile_blackboard_blue.jpg" sf:resource-type="1" sf:hfs-type="0" sf:size="46334"/>
          </sf:unfiltered>
          <sf:extent>
            <sfa:rect sfa:x="0" sfa:y="0" sfa:w="400" sfa:h="400"/>
          </sf:extent>
        </x>
    """
    def __init__(self, path):
        self.path = path

    @staticmethod
    def read(e):
        path = None
        unfiltered = e.find_or_lookup(ns("sf:unfiltered"))
        data = unfiltered.find_or_lookup(ns("sf:data"))
        path = data.get(ns("sf:path"))
        return Bitmap(path)

    def surface_from_data(self, data):
        im = Image.open(StringIO(data))
        im.putalpha(256) # create alpha channel
        arr = numpy.array(im)
        height, width, channels = arr.shape

        r = arr[:,:,0]
        g = arr[:,:,1]
        b = arr[:,:,2]
        a = arr[:,:,3]
        arr = numpy.zeros(arr.shape, dtype=arr.dtype)
        arr[:,:,0] = b
        arr[:,:,1] = g
        arr[:,:,2] = r
        arr[:,:,3] = a

        surface = cairo.ImageSurface.create_for_data(arr, cairo.FORMAT_ARGB32, width, height)
        return surface

    def get_surface(self):
        if self.path not in Bitmap.filename_to_surface:
            if self.path.endswith(".pdf"):
                with utils.tempfile(".pdf") as tmpfile1:
                    fi = open(tmpfile1, "wb")
                    data = Keynote.read_file(self.path) 
                    if data is None:
                        return None
                    fi.write(data)
                    fi.close()
                    if options.pdftoppm:
                        prefix = "/tmp/ppm"+str(os.getpid())
                        os.system("pdftoppm -f 1 -l 1 -r 72 "+tmpfile1+" "+prefix+" >/dev/null 2>&1")
                        ppm = prefix+"-000001.ppm"
                        fi = open(ppm, "rb")
                        data = fi.read()
                        fi.close()
                        os.unlink(ppm)
                    else:
                        with utils.tempfile(".jpeg") as jpeg:
                            os.system("pdf2jpeg -p 1 -r 72 "+tmpfile1+" -o "+jpeg+" >/dev/null 2>&1")
                            fi = open(jpeg, "rb")
                            data = fi.read()
                            fi.close()
            else:
                data = Keynote.read_file(self.path) 
                if data is None:
                    return None
            Bitmap.filename_to_surface[self.path] = self.surface_from_data(data)
        return Bitmap.filename_to_surface[self.path]


class TexturedFill(object):
    """
    <sf:textured-fill sfa:ID="SFDTexturedImageFill-30" sf:technique="fit" xsi:type="textured-fill">
      <sf:filtered-image sfa:ID="SFRFilteredImage-31">
        <sf:unfiltered-ref sfa:IDREF="SFRImageBinary-6"/>
        <sf:extent>
          <sfa:rect sfa:x="0" sfa:y="0" sfa:w="1024" sfa:h="768"/>
        </sf:extent>
      </sf:filtered-image>
    </sf:textured-fill>
    """
    def __init__(self, path):
        self.path = path

    @classmethod
    def is_in_tag(cls, element):
        e = element.find_or_lookup(ns("sf:textured-fill"))
        if e is not None:
            return True

    @classmethod
    def read(cls, xml):
        image = xml.find_or_lookup(ns("sf:textured-fill")) \
                   .find_or_lookup(ns("sf:filtered-image"))
        path = Bitmap.read(image)
        return TexturedFill(path)

    def render(self, device, width, height):
        surface = self.path.get_surface()
        if surface is None:
            return None # file not found
        device.set_source_surface(surface,0,0)
        device.move_to(0,0)
        device.line_to(width,0)
        device.line_to(width,height)
        device.line_to(0,height)
        device.line_to(0,0)
        device.fill()

class PlainFill(object):
    def __init__(self, color):
        self.color = color

    def render(self, device, width, height):
        r,g,b,a = self.color 
        device.set_source_rgba(r,g,b,a)
        device.move_to(0,0)
        device.line_to(width,0)
        device.line_to(width,height)
        device.line_to(0,height)
        device.line_to(0,0)
        device.fill()

class Index(object):
    """
    <?xml version="1.0"?>
    <key:presentation xmlns:sfa="http://developer.apple.com/namespaces/sfa" xmlns:sf="http://developer.apple.com/namespaces/sf" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:key="http://developer.apple.com/namespaces/keynote2" key:version="92008102400" sfa:ID="BGShow-0" key:play-mode="interactive" key:kiosk-slide-delay="5" key:kiosk-build-delay="2" key:mode="once">
      <key:size sfa:w="800" sfa:h="600"/>
    """
    styles = {}
    stylesheets = {}
    master_slides = {}

    def __init__(self, doc, fi):
        Index.current = self
        self.doc = doc
        self.xml = XML(fi.read())
        assert self.xml.tag == ns("key:presentation")
        assert len(Index.styles) == 0
        self.parse_size()
        self.parse_stylesheets()
        self.parse_master_slides()

    @staticmethod
    def add_style_to_registry(id, obj):
        if id in Index.styles:
            raise AssumptionError("ID %s occurs twice" % id)
        Index.styles[id] = obj

    def parse_size(self):
        size = self.xml.find(ns("key:size"))
        self.width = int(size.get(ns("sfa:w")))
        self.height = int(size.get(ns("sfa:h")))

    def parse_stylesheets(self):
        for element in self.xml.iter(ns("key:stylesheet")):
            id = element.get(ns("sfa:ID"))
            Index.stylesheets[id] = Stylesheet(element)

    def parse_master_slides(self):
        for element in self.xml.iter(ns("key:master-slide")):
            id = element.get(ns("sfa:ID"))
            Index.master_slides[id] = Slide(element, None)

    def slides(self):
        return [Slide(child, i+1) for i,child in enumerate(self.xml.find(ns("key:slide-list"))) if utils.is_in_range(i+1,options.pages)]

class Style(dict):
    def __init__(self, styles, id=None, ident=None, parent_ident=None):
        dict.__init__(self)
        self.update(styles)
        self.id = id
        self.ident = ident
        # parent_ident always references something in our parent stylesheet.
        # Hence, it's perfectly possible for parent_ident and ident to be the
        # same string
        self.parent_ident = parent_ident

    def copy(self):
        return Style(dict.copy(self), self.id, self.ident, self.parent_ident)

    def update_no_overwrite(self, other):
        for k,v in other.items():
            if k not in self:
                self[k] = v

    def resolve(self, lookup):
        assert self.parent_ident in lookup
        parent_ident = self.parent_ident
        while parent_ident is not None:
            parent = lookup[parent_ident]
            for k,v in parent.items():
                if k not in self:
                    self[k] = v
            parent_ident = parent.parent_ident

class Stylesheet(object):
    def __init__(self, xml):
        assert xml.tag == ns("key:stylesheet")
        self.id = xml.get(ns("sfa:ID"))
        self.xml = xml
        self.ident_lookup = {}
        self.top_level_styles = None
        self._parse()

    NONE_CANCELS = True
    
    def _style_elem(self, element):

        sfa_parent_ident = element.get(ns("sf:parent-ident"))
        if sfa_parent_ident and self.parent:
            parent = self.parent
            while sfa_parent_ident not in parent.ident_lookup:
                parent = parent.parent
                if parent is None:
                    # TODO: This happens for doc 111707259. The stylesheet's parent
                    #       is a master slide's stylesheet.
                    raise AssumptionError("none of %s's parents have ident %s (referenced from %s)" % (self.id, sfa_parent_ident, sfa_id))
            parent_style = parent.ident_lookup[sfa_parent_ident]
            style = parent_style.copy()
        else:
            ## This is very common
            #if sfa_parent_ident:
            #    warn("parent-ident but no parent")
            style = {}

        def u(name, new):
            if new is not None:
                style[name] = new
            elif self.NONE_CANCELS:
                # I think this is some kind of "explicit inherit"
                style[name] = None

        if element.tag == ns("sf:alignment"):
            u("alignment", Primitives.parse_number(element))
        elif element.tag == ns("sf:fontColor"):
            u("fontColor", Primitives.parse_color(element))
        elif element.tag == ns("sf:fontSize"):
            u("fontSize", Primitives.parse_number(element))
        elif element.tag == ns("sf:fontName"):
            u("fontName", Primitives.parse_string(element))
        elif element.tag == ns("sf:stroke"):
            u("stroke", Primitives.parse_stroke_style(element))
        elif element.tag == ns("sf:fill") and element.has_parent(ns("sf:slide-style")):
            if Primitives.has_color(element):
                u("slide-fill", PlainFill(Primitives.parse_color(element)))
            elif TexturedFill.is_in_tag(element):
                u("slide-fill", TexturedFill.read(element))
        elif element.tag == ns("sf:fill"):
            if Primitives.has_color(element):
                u("fill", Primitives.parse_color(element))
            elif element.find("sf:null") is not None:
                u("fill", None)
            else:
                warn("Unknown fill type %s" % element[0].shorttag)
        else:
            for child in element:
                style.update(self._style_elem(child))

        sfa_id = element.get(ns("sfa:ID"))
        sfa_ident = element.get(ns("sf:ident"))

        if sfa_id is not None:
            s = Style(style, sfa_id, sfa_ident, sfa_parent_ident)
            Index.add_style_to_registry(sfa_id, s)
            if sfa_ident is not None:
                self.ident_lookup[sfa_ident] = s
        elif sfa_ident is not None:
            raise AssumptionError("sfa:indent without sfa:id")
        elif sfa_parent_ident is not None:
            raise AssumptionError("sfa:parent-ref without sfa:id")

        return style

    def _parse(self):
        parent_ref = self.xml.find(ns("sf:parent-ref"))
        if parent_ref is not None:
            idref = parent_ref.get(ns("sfa:IDREF"))
            self.parent = Index.stylesheets[idref]
        else:
            self.parent = None

        self.top_level_styles = self._style_elem(self.xml)

    @staticmethod
    def find_in_tag(xml):
        if xml.tag == "key:stylesheet":
            raise AssumptionError("don't call Stylesheet.find_in_tag on the stylesheet tag itself")
            return Stylesheet(xml)

        stylesheet_ref = xml.find(ns("sf:stylesheet-ref"))
        if stylesheet_ref is not None:
            id_ref = stylesheet_ref.get(ns("sfa:IDREF"))
            return Index.stylesheets[id_ref]

        stylesheet= xml.find(ns("key:stylesheet"))
        if stylesheet is not None:
            # We already parsed this style sheet, so instead of
            # parsing it again, just look it up by its ID
            id = stylesheet.get(ns("sfa:ID"))
            return Index.stylesheets[id]

        return None

class Geometry(object):
    """ A bounding box 
    
        Example data:

        <sf:geometry sfa:ID="SFDAffineGeometry-101" sf:sizesLocked="true">
          <sf:naturalSize sfa:w="289" sfa:h="106"/>
          <sf:size sfa:w="289" sfa:h="106"/>
          <sf:position sfa:x="675" sfa:y="105.5"/>
        </sf:geometry>

        "for shapes, natural size and size are the same" (Work Programming Guide, page 27)
    """
    
    def __init__(self, x, y, width, height, original_width, original_height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.original_width = original_width
        self.original_height = original_height

    @property
    def x1(self):
        return self.x

    @property
    def y1(self):
        return self.y

    @property
    def x2(self):
        return self.x + self.width

    @property
    def y2(self):
        return self.y + self.height

    def get_matrix(self):
        # user space to pattern space
        xx = self.original_width / self.width
        yy = self.original_height / self.height
        x0 = -self.x * xx
        y0 = -self.y * xx
        return cairo.Matrix(x0=x0, y0=y0, xx=xx, yy=yy)

    @staticmethod
    def read(xml):
        geometry = xml.find(ns("sf:geometry"))
        size = geometry.find(ns("sf:size"))
        width = float(size.get(ns("sfa:w")))
        height = float(size.get(ns("sfa:h")))
        position = geometry.find(ns("sf:position"))
        x = float(position.get(ns("sfa:x")))
        y = float(position.get(ns("sfa:y")))
        nsize = geometry.find(ns("sf:naturalSize"))
        original_width = float(nsize.get(ns("sfa:w")))
        original_height = float(nsize.get(ns("sfa:h")))
        return Geometry(x, y, width, height, original_width, original_height)

    def __str__(self):
        return "%dx%d:%d:%d" % (self.width, self.height, self.x, self.y)

class Path(object):
    """A path.

       Example data:

       <sf:path>
         <sf:bezier-path sfa:ID="SFDBezierPathSource-13">
           <sf:bezier sfa:ID="NSBezierPath-25" sfa:path="M 0 0 L 289 0 L 289 
         </sf:bezier-path>
       </sf:path>
    """

    def __init__(self):
        self.beziers = []

    @staticmethod
    def read(xml):
        a = Path()
        path = xml.find_or_lookup(ns("sf:path"))

        bezier = path.find_or_lookup("sf:editable-bezier-path") \
              or path.find_or_lookup("sf:bezier-path")
        if bezier is not None: 
            p = bezier.find("sf:bezier")
            s = p.get("sfa:path")
            a.beziers.append(s)
            return a
            
        point_path = path.find_or_lookup("sf:point-path")
        if point_path is not None:
            type = point_path.get("sf:type")
            point = point_path.find("sf:point")
            size = point_path.find("sf:size")
            x,y = float(point.get("sfa:x")),float(point.get("sfa:y"))
            w,h = float(size.get("sfa:w")),float(size.get("sfa:h"))
            path = Path.create_point_path(type, x, y, w, h)
            if path:
                a.beziers.append(path)
            return a

        callout2_path = path.find_or_lookup("sf:callout2-path")
        if callout2_path is not None:
            warn("Can't parse callout2_path yet")
            return a

        warn("Unknown path type %s" % path[0].shorttag)
        return a

    @staticmethod
    def create_point_path(type, x, y, w, h):
        io = StringIO()
        if type == "star":
            io.write("M %f %f " % (x, y))
            io.write("L %f %f " % (x+w, y))
            io.write("L %f %f " % (x+w, y+h))
            io.write("L %f %f " % (x, y+h))
            io.write("L %f %f " % (x, y))
            io.write("Z")
            return io.getvalue().strip()
        else:
            warn("Unknown point path type: %s" % type)
            return None

    def apply(self, device, x0, y0):
        for bezier in self.beziers:
            i = 0
            items = bezier.split(" ")
            while i<len(items):
                cmd = items[i]
                if cmd == "M":
                    x = float(items[i+1])
                    y = float(items[i+2])
                    i += 3
                    device.move_to(x0 + x, y0 + y)
                elif cmd == "L":
                    x = float(items[i+1])
                    y = float(items[i+2])
                    i += 3
                    device.line_to(x0 + x, y0 + y)
                elif cmd == "C":
                    x1 = float(items[i+1])
                    y1 = float(items[i+2])
                    x2 = float(items[i+3])
                    y2 = float(items[i+4])
                    x3 = float(items[i+5])
                    y3 = float(items[i+6])
                    i += 7
                    device.curve_to(x0 + x1, y0 + y1,
                                     x0 + x2, y0 + y2,
                                     x0 + x3, y0 + y3)
                elif cmd == "Z":
                    i += 1
                    pass

class StyleState(dict):
    """ A style object or reference.

        Example data:

        <sf:style>
          <sf:graphic-style-ref sfa:IDREF="SFDGraphicStyle-63"/>
        </sf:style>
    """
    def __init__(self, d=None):
        dict.__init__(self)
        if d is not None:
            self.update(d)

    def copy(self):
        return StyleState(dict.copy(self))

    def _add_reference(self, id, stylesheet):
        """ Lookup a style by identifier.
            Used e.g. for <sf:p sf:style>.
        """
        if id in Index.styles:
            self.update(Index.styles[id])
        else:
            # E.g. 98219213 uses sf:style to reference a ident
            self.update(stylesheet.ident_lookup[id])
        return self

    def add_from_reference(self, id, stylesheet):
        """ Return a new style which also 
            contains the referenced attributes """
        return self.copy()._add_reference(id, stylesheet)

    def add_indent(self, indent):
        return self # FIXME

    @staticmethod
    def read(xml, stylesheet):
        # Notice: This element seems to be optional.
        """
        <sf:style>
          <sf:graphic-style-ref sfa:IDREF="SFDGraphicStyle-62"/>
        </sf:style>
        """
        assert xml.get(ns("sf:style")) is None
        assert xml.get(ns("sfa:style")) is None

        s = StyleState()
        for style in xml.findall(ns("sf:style")):
            # sf:style tags have one element, a style reference
            # (<graphic-style-ref> etc.)
            assert len(style) == 1
            reftag = style[0]
            s._add_reference(reftag.get(ns("sfa:IDREF")), stylesheet)
        return s


class Text(object):
    """ A text object.

        Example data:

        <sf:text sfa:ID="SFWPFrame-13" sf:layoutstyle="SFWPLayoutStyle-263">
          <sf:text-storage sfa:ID="SFWPStorage-13" sf:kind="textbox" sf:excl="
            <sf:stylesheet-ref sfa:IDREF="SFSStylesheet-16"/>
            <sf:text-body>
              <sf:p sf:style="SFWPParagraphStyle-413">filled text box<sf:br/><
              <sf:p sf:style="SFWPParagraphStyle-413">with reflection</sf:p>
            </sf:text-body>
          </sf:text-storage>
        </sf:text>
    """

    def __init__(self):
        self.content = []


    def text(self, text, styles):
        self.content.append(("text", text, styles))

    def br(self, styles):
        self.content.append(("br", None, styles))

    def recurse(self, e, styles, stylesheet):
        if e.tag == ns("sf:p"):
            styles = styles.add_from_reference(e.get(ns("sf:style")), stylesheet)
            list_level = e.get(ns("sf:list-level"))
            if list_level is not None:
                styles = styles.add_indent(int(list_level))
            if e.text is not None:
                self.text(e.text, styles)
            for child in e:
                self.recurse(child, styles, stylesheet)
            if e.tail is not None:
                self.text(e.tail, styles)
        elif e.tag == ns("sf:span") or e.tag == ns("sf:layout"):
            assert e.get(ns("sfa:style")) is None # can this happen?
            if e.text is not None:
                inside_styles = styles.add_from_reference(e.get(ns("sf:style")), stylesheet)
                self.text(e.text, inside_styles)
            if e.tail is not None:
                self.text(e.tail, styles)
        elif e.tag == ns("sf:br"):
            self.br(styles)
        elif e.tag == ns("sf:tab"):
            self.text("\t", styles)
        elif e.tag == ns("sf:lnbr"):
            self.br(styles)
        elif e.tag == ns("sf:crbr"):
            self.br(styles)
        elif e.tag == ns("sf:intratopicbr"):
            self.br(styles)
        elif e.tag == ns("sf:link") or e.tag == ns("sf:link-ref"):
            # TODO: implement links
            for child in e:
                self.recurse(child, styles, stylesheet)
        else:
            print(xml_to_string(e))
            raise AttributeError("Unknown tag within paragraph: %s" % e.tag)

    @staticmethod
    def read(xml, graphics_style):
        text = Text()
        sf_text = xml.find(ns("sf:text"))
        if sf_text is None:
            return None
        for storage in sf_text.iter(ns("sf:text-storage")):
        #for storage in sf_text.iter_with_lookup(ns("sf:text-storage")):
            stylesheet = Stylesheet.find_in_tag(storage)
            assert stylesheet is not None
            for textbody in storage.findall(ns("sf:text-body")):
                text_style = StyleState.read(textbody, stylesheet)

                # combine the two styles (TODO: does graphics_style actually
                # ever have any styles that are interesting for text rendering?)
                style = graphics_style
                style.update(text_style)

                assert textbody.text is None
                for child in textbody:
                    text.recurse(child, style, stylesheet)
        return text

# allow text in text boxes to extend a tiny bit beyond the bounds
TEXTBOX_X_OVERFLOW_LEEWAY = 10

ALIGN_LEFT  = 0
ALIGN_RIGHT = 1
ALIGN_CENTER  = 2

class Drawable(object):
    def __init__(self, geometry, style, path, text):
        self.geometry = geometry
        self.style = style
        self.path = path
        self.text = text
       
    def render_text(self, device):
        x = 0
        y = 0

        # we want one initial advance
        has_line = True 

        for type,text,style in self.text.content:

            # TODO: what are the defaults for these?
            device.set_font_face(find_cairo_font("Comic Sans MS"))
            device.set_font_size(12)
            device.set_source_rgba(0,0,0,0)

            if style.get("fontName") is not None:
                ttf = find_cairo_font(style["fontName"])
                device.set_font_face(ttf)
            if style.get("fontSize") is not None:
                device.set_font_size(style["fontSize"])
            if style.get("fontColor") is not None:
                r,g,b,a = style["fontColor"]
                device.set_source_rgba(r,g,b,a)

            alignment = style.get("alignment", 0)

            (ascent, descent, height, 
             max_x_advance, max_y_advance) = device.font_extents()

            if type == "br":
                if has_line:
                    x = 0
                    y += ascent + descent
                    has_line = False
            elif type == "text":
                words = text.split(" ")
                (space_x_bearing, space_y_bearing, space_width, 
                 space_height, space_x_advance, space_y_advance) = device.text_extents(" ")

                line = []
                lines = [line]

                # FIXME: we don't always start with x = 0 if the style switching within a line
                x = 0
                for word in words:
                    if not len(word):
                        continue
                    (x_bearing, y_bearing, width, 
                     height, x_advance, y_advance) = device.text_extents(word)
                    w = width - x_bearing - TEXTBOX_X_OVERFLOW_LEEWAY

                    if x > 0 and x + w > self.geometry.width:
                        x = 0
                        line = [word]
                        lines.append(line)
                    else:
                        x += x_advance + space_x_advance
                        line.append(word)
                
                first = True
                for line in lines:
                    if has_line:
                        y += ascent + descent
                        has_line = False
                    first = False

                    text = " ".join(line)
                    (x_bearing, y_bearing, width, 
                     height, x_advance, y_advance) = device.text_extents(text)

                    if alignment == ALIGN_LEFT or alignment is None:
                        x = 0
                    elif alignment == ALIGN_RIGHT:
                        x = self.geometry.width - width
                    elif alignment == ALIGN_CENTER:
                        x = (self.geometry.width - width) / 2

                    device.move_to(self.geometry.x + x, 
                                   self.geometry.y + y)
                    x += x_advance
                    device.show_text(text)
                    has_line = True

    def render_path(self, device):
        if self.path is None:
            return
        fill = self.style.get("fill")
        stroke = self.style.get("stroke")
        if fill is None and stroke is None:
            return
        g = self.geometry
        self.path.apply(device, g.x, g.y)
        if fill:
            device.set_source_rgba(*fill)
            device.fill()
        if stroke:
            stroke.apply(device)
            device.stroke()

    def has_text(self):
        return len(self.text.content)

    def render(self, device):
        if not self.has_text():
            self.render_path(device)
        self.render_text(device)

class Media(object):
    """
        <sf:media sfa:ID="SFDImageInfoDowngrader-0" sf:placeholder="true" key:inheritance="prototype" key:tag="Media">
          <sf:geometry sfa:ID="SFDAffineGeometry-16" sf:aspectRatioLocked="true">
            ....
          </sf:geometry>
          <sf:style>
            <sf:graphic-style-ref sfa:IDREF="SFDGraphicStyle-16"/>
          </sf:style>
          <sf:placeholder-size sfa:w="496" sfa:h="279"/>
          <sf:masking-shape-path-source>
            <sf:bezier-path sfa:ID="SFDBezierPathSource-0">
                ...
            </sf:bezier-path>
          </sf:masking-shape-path-source>
          <sf:crop-geometry sfa:ID="SFDAffineGeometry-17" sf:sizesLocked="true" sf:aspectRatioLocked="true">
            ...
          </sf:crop-geometry>
          <sf:content>
            <sf:image-media sfa:ID="SFDImageMedia-0">
              <sf:filtered-image sfa:ID="SFRFilteredImage-30">
                <sf:unfiltered sfa:ID="SFRImageBinary-10">
                  <sf:size sfa:w="650" sfa:h="650"/>
                  <sf:data sfa:ID="SFEData-22" sf:path="Shared/caterpillar and leaf.jpg" sf:displayname="caterpillar and leaf.jpg" sf:resource-type="1" sf:hfs-type="1246774599" sf:size="65589"/>
                </sf:unfiltered>
                <sf:extent>
                  <sfa:rect sfa:x="0" sfa:y="0" sfa:w="650" sfa:h="650"/>
                </sf:extent>
              </sf:filtered-image>
            </sf:image-media>
          </sf:content>
        </sf:media>
    """
    def __init__(self, geometry, style, bitmap):
        self.geometry = geometry
        self.style = style
        self.bitmap = bitmap

    def render(self, device):
        g = self.geometry

        surface = self.bitmap.get_surface()
        if surface is None:
            return None # file not found
        pattern = cairo.SurfacePattern(surface)
        pattern.set_matrix(g.get_matrix())
        device.set_source(pattern)

        device.move_to(g.x1,g.y1)
        device.line_to(g.x2,g.y1)
        device.line_to(g.x2,g.y2)
        device.line_to(g.x1,g.y2)
        device.line_to(g.x1,g.y1)
        device.fill()

class Slide(object):
    def __init__(self, xml, nr):
        self.nr = nr
        self.id = xml.get(ns("sfa:ID"))
        self.xml = xml
        if self.nr is not None:
            info("Parsing slide %d" % self.nr)
        else:
            info("Parsing master slide")
        info(" %s (%s)" % (self.xml.shorttag, self.id))
        self.stylesheet = Stylesheet.find_in_tag(xml)
        self.parse_drawables()
        self.master = self.parse_master()
        info(" master slide: %s" % self.master)

        if self.stylesheet is None:
            info("  Slide has no stylesheet")
        else:
            info("  Slide stylesheet: %s" % self.stylesheet.id)

    def parse_master(self):
        master_ref = self.xml.find(ns("key:master-ref"))
        if master_ref is None:
            return None
        return Index.master_slides[master_ref.get(ns("sfa:IDREF"))]

    def parse_drawable(self, e):
        if e.tag == ns("sf:shape"):
            geometry = Geometry.read(e)
            style = StyleState.read(e, self.stylesheet)
            path = Path.read(e)
            text = Text.read(e, style)
            self.drawables.append(Drawable(geometry, style, path, text))
        elif e.tag == ns("sf:media"):
            geometry = Geometry.read(e)
            style = StyleState.read(e, self.stylesheet)
            #<sf:masking-shape-path-source>
            #<sf:crop-geometry sfa:ID="SFDAffineGeometry-17" sf:sizesLocked="true" sf:aspectRatioLocked="true">

            f = e.find(ns("sf:content")) \
                 .find_or_lookup(ns("sf:image-media")) \
                 .find_or_lookup(ns("sf:filtered-image"))
            image = Bitmap.read(f)

            self.drawables.append(Media(geometry, style, image))
        elif e.tag == ns("sf:group"):
            for child in e:
                self.parse_drawable(child)
        elif e.tag == ns("sf:geometry"):
            # sf:group has a number of subelements and a sf:geometry
            assert e.getparent().tag == ns("sf:group")
        elif "placeholder" in e.tag:
            # title-placeholder-ref, body-placeholder-ref etc.
            #
            # these seem to almost always be empty, and seem to always
            # be refs.
            pass
        else:
            warn("Drawable %s not supported yet" % e.shorttag)

    def parse_drawables(self):
        """
            <sf:drawables sfa:ID="NSMutableArray-4779">
              <sf:shape sfa:ID="BGShapeInfo-159" can-autosize-v="true">
                 ...
              </sf:shape>
        """
        self.drawables = []

        page = self.xml.find(ns("key:page"))
        #for drawable in self.xml.iter(ns("sf:drawables")):
        for drawable in page.iter_with_lookup(ns("sf:drawables")):
            for e in drawable:
                self.parse_drawable(e)

    def _render_background(self, device):
        styles = self.stylesheet.top_level_styles
        fill = styles.get("slide-fill")
        fill.render(device, Index.current.width, Index.current.height)

    def render(self, device):
        if self.master is not None:
            self.master._render_drawables(device)
        self._render_drawables(device)
        device.show_page()
    
    def _render_drawables(self, device):
        # FIXME: do both the master-slide as well as the slide get to
        #        render their background color?
        self._render_background(device)
        for drawable in self.drawables:
            drawable.render(device)

    def __str__(self):
        return self.xml.shorttag + " (" + self.id + ")"
