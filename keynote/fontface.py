import ctypes
from ctypes import c_int, c_void_p, c_char_p
import cairo
from functools import lru_cache

NULL = c_void_p() 
FT_ERR_OK = 0
class Freetype:
    """ Interface to Freetype, using ctypes. """
    def __init__(self):
        """ Create a Freetype loader object. Initialization is lazy, and will
            happen with the first call to load_font() """
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        self.so = ctypes.CDLL("libfreetype.so.6")
        self._ft_lib = c_void_p()
        if FT_ERR_OK != self.so.FT_Init_FreeType(ctypes.byref(self._ft_lib)):
            raise "Error initialising FreeType library."

    def load_font(self, filename, faceindex=0):
        """ Load a font file from disk. 
            For .ttc files (font collections), load_font accepts an optional parameter 
            "index" for specifying which font to load from the collection.
            Returns a ctypes void pointer. """
        self._initialize()
        ft_face = c_void_p()
        filename_p = c_char_p(bytes(filename))
        status = self.so.FT_New_Face(self._ft_lib, filename_p, faceindex, ctypes.byref(ft_face))
        if FT_ERR_OK != status:
            raise Exception("Error creating FreeType font face for " + filename + ": "+str(status))
        return ft_face
_freetype = Freetype()

class Cairo:
    """ Interface to Cairo fonts, using ctypes. """
    def __init__(self):
        """ Create a Cairo loader object. Initialization is lazy, and will
            happen with the first call to load() """
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        self.so = ctypes.CDLL("libcairo.so.2")
        self.so.cairo_ft_font_face_create_for_ft_face.restype = c_void_p
        self.so.cairo_ft_font_face_create_for_ft_face.argtypes = [c_void_p, ctypes.c_int]
        self.so.cairo_set_font_face.argtypes = [c_void_p, c_void_p]
        self.so.cairo_font_face_status.argtypes = [c_void_p]
        self.so.cairo_status.argtypes = [c_void_p]

        class PycairoContext(ctypes.Structure):
            _fields_ = [("PyObject_HEAD", ctypes.c_byte * object.__basicsize__),
                    ("ctx", c_void_p),
                    ("base", c_void_p)]
        self.PycairoContext = PycairoContext

        self._surface = cairo.ImageSurface(cairo.FORMAT_A8, 0, 0)
        self._initialized = True

    def load(self, filename, loadoptions=0):
        """ Load a font file. 
            Returns a cairo.FontFace object."""
        self._initialize()
        CAIRO_STATUS_SUCCESS = 0
    
        cairo_ctx = cairo.Context(self._surface)
        cairo_t = self.PycairoContext.from_address(id(cairo_ctx)).ctx
        
        ft_face = _freetype.load_font(filename)

        # create cairo font face for freetype face
        cr_face = self.so.cairo_ft_font_face_create_for_ft_face(ft_face, loadoptions)
        if CAIRO_STATUS_SUCCESS != self.so.cairo_font_face_status(cr_face):
            raise Exception("Error creating cairo font face for " + filename)

        self.so.cairo_set_font_face(cairo_t, cr_face)
        if CAIRO_STATUS_SUCCESS != self.so.cairo_status(cairo_t):
            raise Exception("Error creating cairo font face for " + filename)

        face = cairo_ctx.get_font_face()
        return face
_cairo = Cairo()

FC_FAMILY = "family".encode("ascii")
FC_STYLE = "style".encode("ascii")
FC_OUTLINE = "outline".encode("ascii")
FC_SCALABLE = "scalable".encode("ascii")
FC_FILE = "file".encode("ascii")
# FcType
FcTypeVoid = 0
FcTypeInteger = 1
FcTypeDouble = 2
FcTypeString = 3
FcTypeBool = 4
FcTypeMatrix = 5
FcTypeCharSet = 6
FcTypeFTFace = 7
FcTypeLangSet = 8
FcTrue = 1
FcFalse = 0
FcSetSystem = 0
# FcResult
FcResultMatch = 0
FcResultNoMatch = 1
FcResultTypeMismatch = 2
FcResultNoId = 3
FcResultOutOfMemory = 4
# FcMatch
FcMatchPattern=0
FcMatchFont=1
FcMatchScan=2

class FontConfigError(Exception):
    pass
class FcFontSet(ctypes.Structure):
    _fields_ = [("nfonts", ctypes.c_int),
                ("cfonts", ctypes.c_int),
                ("fonts", ctypes.POINTER(c_void_p)),
               ]
class FontConfig:
    """ Interface to fontconfig, using ctypes. """
    def __init__(self):
        self._initialized = False
        self.so= None
        self._fc_current = None

    def _initialize(self):
        if self._initialized:
            return
        self.so = ctypes.CDLL("libfontconfig.so")
        self.so.FcConfigGetCurrent.restype = c_void_p
        self.so.FcFontSort.restype = ctypes.POINTER(FcFontSet)
        if not self.so.FcInit():
            raise FontConfigError("Couldn't initialize fontconfig")
        self._fc_current = self.so.FcConfigGetCurrent();
        if not self._fc_current:
            raise FontConfigError("fontconfig configuration failed")
        self._set =  self.so.FcConfigGetFonts(self._fc_current, FcSetSystem);
        if not self._set:
            raise FontConfigError("fontconfig doesn't have any fonts")
        self._initialized = True

    def add_font(self, filename):
        """ Adds a font to fontconfig. Fonts added this way will later
            be returned by find_font() if they match the requested pattern.
            Notice that a properly configured installation of fontconfig 
            comes with a default set of system fonts, so you only need to
            call this method if you want to add your own set of custom
            fonts, e.g. for fonts embedded in document files.
        """
        if not self.so.FcConfigAppFontAddFile(self._fc_current, filename.encode("ascii")):
            raise FontConfigError("Failed to Add font file %s." % filename)

    def find_font(self, family, style=None, faceindex=0):
        """ Searches for a given font family, with the optional style.
            This method will always return a font, regardless of how well
            it matches a pattern. If it doesn't find a match for the specified
            family, it will just match by style, or just return the default
            font if that fails as well.
        """
        self._initialize()

        if style:
            pattern = self.so.FcPatternBuild(NULL,
                FC_OUTLINE, FcTypeBool, FcTrue,
                FC_SCALABLE, FcTypeBool, FcTrue, 
                FC_FAMILY, FcTypeString, family.encode("ascii"), 
                FC_STYLE, FcTypeString, style, 
                NULL)
        else:
            pattern = self.so.FcPatternBuild(NULL,
                FC_OUTLINE, FcTypeBool, FcTrue,
                FC_SCALABLE, FcTypeBool, FcTrue, 
                FC_FAMILY, FcTypeString, family.encode("ascii"), 
                NULL)


        self.so.FcConfigSubstitute(0, pattern, FcMatchPattern); 
        self.so.FcDefaultSubstitute(pattern);

        result = 0
        fcset_ptr = self.so.FcFontSort(NULL, pattern, c_int(1), NULL, NULL)
        if not fcset_ptr:
            self.so.FcPatternDestroy(pattern)
            return None
        fcset = fcset_ptr.contents

        filename = None
        candidates = []
        for i in range(fcset.nfonts):
            m = fcset.fonts[i]
            m_filename = c_char_p(0)
            m_family = c_char_p(0)
            m_style = c_char_p(0)
            ok = self.so.FcPatternGetString(m, FC_FILE, c_int(0), ctypes.byref(m_filename))
            ok |= self.so.FcPatternGetString(m, FC_FILE, c_int(0), ctypes.byref(m_family))
            ok |= self.so.FcPatternGetString(m, FC_FILE, c_int(0), ctypes.byref(m_style))
            if ok != FcResultMatch:
                continue
            candidates.append((m_filename.value, m_family.value, m_style.value))
        self.so.FcPatternDestroy(pattern)

        # try by family and style
        for m_filename,m_family,m_style in candidates:
            if m_family.lower() == family.lower() and m_style == style:
                return m_filename
        # try just by family
        for m_filename,m_family,m_style in candidates:
            if m_family.lower() == family.lower():
                return m_filename
        # try just by style
        for m_filename,m_family,m_style in candidates:
            if m_style == style:
                return m_filename
        # return "default" font
        for m_filename,m_family,m_style in candidates:
            return m_filename
_font_config = FontConfig()

@lru_cache()
def create_cairo_font_face_for_file(filename):
    """ returns a cairo font face for a font file. """
    return _cairo.load(filename)

@lru_cache()
def find_cairo_font(name):
    """ Tries to find the font with the given name using fontconfig,
        and if successful, returns a cairo fontface object that 
        contains the font."""
    if "-" in name:
        name = name[0:name.find("-")]
    if name not in cache:
        path = _font_config.find_font(name)
        cache[name] = _cairo.load(path)
    return cache[name]

if __name__ == "__main__":
    path = _font_config.find_font("Arial")
    if path is None:
        import sys
        sys.exit(1)
    ft = create_cairo_font_face_for_file(path)
    print(ft)

