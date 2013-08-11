#!/usr/bin/python
import re
import zlib
import io
import warnings
try:
    import hashlib as md5
except ImportError:
    import md5

class PDFMalformedException(Exception):
    """ thrown if we detect an error in a PDF """
    pass

def error(s):
    raise PDFMalformedException(s)

class ID:
    """ A PDF ID is a tuple of object id and generation number. The
        combination of both uniquely identifies an object.
        Generation numbers are usually increased if a file is updated,
        but in most cases, they are zero.
    """
    def __init__(self, id, gen):
        self.id = int(id)
        self.gen = int(gen)
    def __hash__(self):
        return hash(self.id)
    def __eq__(self, other):
        return self.id == other.id
    def __str__(self):
        return "%d %d R" % (self.id, self.gen)
    def __repr__(self):
        return "ID(%d,%d)" % (self.id, self.gen)
    def obj(self):
        return "%d %d obj" % (self.id, self.gen)

class PDFType:
    pass

class PDFString(PDFType):
    """ A pdf string object is a sequence of bytes (unsigned integers, 0-255).
        They're delimited by '(' and ')'.
        Examples:
          (Hello World)
          (String with (balanced) inner parentheses)
    """
    def __init__(self, s):
        self.s = s
    def raw(self):
        s = io.StringIO()
        level = 0
        e = 0
        o = 0
        for c in self.s:
            if not e and c == '(':
                o += 1
                if o>1:
                    s.write(e)
            elif not e and c == ')':
                if o>1:
                    s.write(e)
                o -= 1
            elif e:
                if c in "abcdefghijklmnopqrstuvwxyz":
                    s.write(eval('"\\%s"' % c))
                elif c in "()\\":
                    s.write(c)
                else:
                    error("invalid escape sequence in literal string: \\"+repr(c))
                e = 0
            elif c == '\\':
                e = 1
            else:
                s.write(c)
        return s.getvalue()
            
    def __str__(self):
        return self.s

class PDFHexID(PDFType):
    """ PDF Strings can be encoded hexadecimal, enclosed by '<' and '>'.  
        Example: 
            <0decaf>
    """
    def __init__(self, a):
        self.a = a
    def raw(self):
        s = io.StringIO()
        for c in self.a:
            s.write(chr(c))
        return s.getvalue()
    def __str__(self):
        s = io.StringIO()
        for x in self.a:
            s.write("%02x" % x)
        return "<"+s.getvalue()+">"

class PDFArray:
    """ PDF arrays are a sequential, inhomogeneous collection of objects
        and primitives, enclosed by '[' and ']'. 
        E.g.: 
          [3 4.1 true (string) /Identifier]
    """
    def __init__(self, a):
        self.a = a
    def __str__(self):
        return "["+(" ".join([str(e) for e in self.a]))+"]"
    def __getitem__(self, i):
        return self.a[i]
    def __repr__(self):
        return repr(self.a)
    def __len__(self):
        return len(self.a)
    def copy(self):
        return PDFArray(self.a[:])
    def append(self, obj):
        self.a.append(obj)
    def prepend(self, obj):
        self.a = [obj] + self.a

class PDFDict(PDFType):
    """ PDF dictionaries are a mapping from keys to arbitrary objects and values,
        enclosed by '<<' and '>>'.
        E.g.:
          << 
            /Type /Page
            /Contents 3 0 R
            /Resources 1 0 R
            /MediaBox [0 0 612 792]
          >>
    """
    def __init__(self, d):
        self.d = d
    def __getitem__(self, id):
        if id not in self.d and not id.startswith("/"):
            return self.d["/"+id]
        return self.d[id]
    def __setitem__(self, key, value):
        self.d[key] = value
    def copy(self):
        return PDFDict(self.d.copy())
    def items(self):
        return self.d.items()
    def __delitem__(self, id):
        del self.d[id]
    def __contains__(self, id):
        return id in self.d
    def __str__(self):
        return "<<\n"+("".join([str(key)+" "+str(value)+"\n" for key,value in self.d.items()]))+">>"
    def get(self,key,value):
        return self.d.get(key,value)

class PDFObject(PDFType):
    """ PDF objects are the top-level data items the file is structured in,
        enclosed by 'obj' and 'endobj'.
        They're similar to tags in tagged file formats like PNG. Every PDF
        object has an ID, which is also the key in the PDF's "xref" table,
        a lookup table that specifies the location of every object in the file.

        While PDF objects are usually dictionaries, they can be arbitrary data 
        structures, like e.g. arrays.

        Example:
          9 0 obj <<
            ...
          >> endobj

        This implementation uses PDFDictObject to special-case dictionary objects.
    """
    def __init__(self, file, id, d):
        self.file = file
        self.id = id
        self.d = d
    def __str__(self):
        return str(self.id)
    def decompress(self):
        pass
    def changed(self):
        self.file.changed(self)
    def obj(self):
        return self.id.obj()+" "+str(self.d)+" endobj\n"
    def get_type(self):
        return ""

def check(b, s):
    if not b: 
        error(s)

class PDFDictObject(PDFDict,PDFObject):
    def __init__(self, file, id, d, stream, encrypted):
        PDFObject.__init__(self, file, id, d)
        if isinstance(d, PDFDict):
            d = d.d
        PDFDict.__init__(self, d)
        self.id = id
        self.stream = stream

        self.encrypted = encrypted
        self.filters = self.d.get("/Filter", [])
        if type(self.filters)!=list and not isinstance(self.filters, PDFArray):
            self.filters = [self.filters]
        try: del self.d["/Length"]
        except: pass
        try: del self.d["/Filter"]
        except: pass
    
    def get_type(self):
        return self.d.get("/Type","")

    def _predict(self, indata, params):
        if "/Predictor" not in params:
            return data
        p = params["/Predictor"]
        if p==1:
            return
        width = params["/Columns"]
        check(not len(indata)%(width+1), "data with row size %d not divisible by %d" % (width,width+1))
        height = len(indata)/(width+1)

        data = []
        if p in [10,11,12,13,14,15]:
            prev = [0]*width
            for pos in range(0,len(indata),(width+1)):
                p = ord(indata[pos])
                pos += 1
                row = [0]*width
                if p==0:
                    row = indata[pos:pos+width]
                elif p==1:
                    row[0]=indata[pos]
                    for i in range(1, width):
                        row[i] = (row[i-1]+ord(indata[pos+i]))&0xff
                elif p==2:
                    for i in range(0, width):
                        row[i] = (prev[i]+ord(indata[pos+i]))&0xff
                prev = row
                data += row
        else:
            error("unknown predictor %d" % p)
        check(len(data) == width*height, "bad data size in predictor output")
        return "".join(map(chr,data)) #XXX: not sure how fast this is

    def _decompress(self):
        filter,self.filters = self.filters[0],self.filters[1:]

        if filter=="/FlateDecode":
            try:
                self.stream = str(zlib.decompress(self.stream.encode("latin-1")), "latin-1")
            except zlib.error as e:
                error("couldn't decompress obj %s: %s" % (str(self.id),str(e)))
                
            if "/DecodeParms" in self.d:
                self.stream = self._predict(self.stream, self.d["/DecodeParms"])
        elif filter=="/ASCII85Decode":
            chars = '!"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstu'
            newstream = ""
            s = 0
            count = 0
            for c in self.stream:
                if c in WHITESPACECHARS:
                    continue
                if c=='z':
                    check(not count, "bad mix of 5-tuples and 'z' in ascii85 stream")
                    newstream += "\0"
                    continue
                if c=='~': # ~> is the eod marker
                    if count:
                        # untested
                        for j in range(count,5):
                            s*=85
                        if count==2: stream+="%c" % ((s>>24)&0xff)
                        elif count==3: stream+="%c%c" % ((s>>24)&0xff,(s>>16)&0xff)
                        elif count==4: stream+="%c%c%c" % ((s>>24)&0xff,(s>>16)&0xff,(s>>8)&0xff)
                    break
                check(c in chars, "bad char in ascii85 stream (%c)" % c)
                s*=85
                s+=ord(c)-ord('!')
                count+=1
                if count==5:
                    newstream += "%c%c%c%c" % ((s>>24)&0xff,(s>>16)&0xff,(s>>8)&0xff,(s>>0)&0xff)
                    s = 0
                    count = 0
            self.stream = newstream
        else:
            self.filters = [filter] + self.filters
            raise ValueError("can't handle compression mode '%s'" % str(filter))

    def decompress(self):
        if self.encrypted:
            self.stream = self.file.securityhandler.decrypt(self.stream, self.id)
            self.encrypted = False
        while self.filters:
            try:
                self._decompress()
            except ValueError:
                # compress as far as possible
                return

    def __str__(self):
        return str(self.id)

    def obj(self):
        d = PDFDict.copy(self)
        stream = None 
        if self.stream:
            stream = self.stream
            if self.file.encrypt and not self.encrypted:
                stream = self.file.securityhandler.encrypt(self.stream, self.id)
            d["/Length"] = len(stream)
        if self.filters:
            if len(self.filters)==1:
                d["/Filter"] = self.filters[0]
            else:
                d["/Filter"] = PDFArray(self.filters)
        s = self.id.obj()+" "+str(d)
        if stream:
            s += " stream\n"+stream+"endstream\nendobj\n"
        else:
            s += " endobj\n"
        return s

class PDFObjectStreamReference:
    def __init__(self, id, pos):
        self.id = id
        self.pos = pos

class PDFPage(PDFDictObject):
    def __init__(self, *args, **kwargs):
        PDFDictObject.__init__(self, *args, **kwargs)
        self.parent = None

    def get_content(self):
        try:
            c = self.d["/Contents"]
        except KeyError:
            c = self.d["/Contents"] = PDFArray([])
        return c

    def get_size(self):
        node = self
        bounds = None
        while node:
            for box in ["/CropBox","/MediaBox","/BleedBox","/ArtBox"]:
                if box in node.d:
                    b = node.d[box]
                    if isinstance(b,ID):
                        b = self.file.get_object(b).d
                    if bounds:
                        bounds = [max(bounds[0],b[0]), max(bounds[1],b[1]),
                                  min(bounds[2],b[2]), min(bounds[3],b[3])]
                    else:
                        bounds = b[:]
            if hasattr(node, "parent"):
                node = node.parent
            else:
                break
        return bounds or [0,0,0,0]

    def prepend_content(self, add):
        c = self.get_content()
        if isinstance(c, ID):
            o = self.file.get_object(c)
            if isinstance(o.d, PDFArray): # indirect array reference
                self.d["/Contents"] = o.d.copy()
                self.d["/Contents"].prepend(add)
            else: # single page stream
                self.d["/Contents"] = PDFArray([add,c])
        elif isinstance(c, PDFArray):
            self.d["/Contents"].prepend(add)
        else:
            file.error("Bad contents value "+repr(c))
        self.changed()

    def append_content(self, add):
        c = self.get_content()
        if isinstance(c, ID):
            o = self.file.get_object(c)
            if isinstance(o.d, PDFArray): # indirect array reference
                self.d["/Contents"] = o.d.copy()
                self.d["/Contents"].append(add)
            else:
                self.d["/Contents"] = PDFArray([c,add])
        elif isinstance(c, PDFArray):
            self.d["/Contents"].append(add)
        else:
            file.error("Bad contents value "+repr(c))
        self.changed()

def upcast(obj,default_type=None):
    if "/Type" in obj.d:
        t = obj.d["/Type"]
    elif default_type:
        if default_type=="/Page" and "/Kids" in obj:
            default_type = "/Pages"
        warnings.warn("no /Type in object: assuming %s" % default_type)
        t = default_type
    else:
        t = None
    if t:
        if t == "/Page":
            return PDFPage(obj.file,obj.id,obj.d,obj.stream,obj.encrypted)
    return obj

NEWLINE = re.compile("[\n\r]")
WHITESPACE = re.compile("[\t\n\r\f\0 ]+")
TOKEN = re.compile(r'<<|>>|\(|[0-9]+\s+[0-9]+\s+R|/[^/ \n\r\t()<>\[\]{}%]*|\[|\]|<|[a-zA-Z0-9._-]+')
NUMBER = re.compile(r'-?[0-9-]*[.][0-9]*(e[0-9-.]+)?|-?[0-9]+e[0-9.-]+')
INTEGER = re.compile(r'-?[0-9]+')
OBJ = re.compile(r'([0-9]+)\s+([0-9]+)\s+obj')
OBJ_START = re.compile(r'[0-9]+\s+[0-9]+\s+obj.*')
REF = re.compile(r'[0-9]+\s+[0-9]+\s+R')
COMMENT = re.compile(r'%[^\n\r]*[\n\r]')
INLINE_XREF = re.compile(r'xref\s+[0-9]+\s+[0-9]+')
XREF = re.compile(r'(xref|ref|ef|f|.xref|..xref|...xref).*')
XREF_START = re.compile(r'xref.*')
TRAILER = re.compile(r'trailer\s*<<')
WHITESPACECHARS = " \n\t\r\f\0"
pad = "\x28\xbf\x4e\x5e\x4e\x75\x8a\x41\x64\x00\x4e\x56\xff\xfa\x01\x08\x2e\x2e\x00\xb6\xd0\x68\x3e\x80\x2f\x0c\xa9\xfe\x64\x53\x69\x7a"

def rc4(text, key=""):
    if not text:
        return text

    p = range(256)
    
    # "encrypt" the key
    right = 0
    for left in xrange(256):
        right += p[left] 
        if len(key):
            pos = left%len(key)
            right += ord(key[pos])
        right &= 0xff
        p[left],p[right] = p[right],p[left]

    # encrypt/decrypt the string
    s = io.StringIO(len(text))
    right = 0
    for pos,c in enumerate(text):
        c = ord(c)
        left = (pos+1)&0xff
        right = (right+p[left])&0xff
        p[left],p[right] = p[right],p[left]
        s.write(chr(c^p[(p[left]+p[right])&0xff]))
    return s.getvalue()

def print_hex(s):
    for i,c in enumerate(s):
        print("%02x" % ord(c),)
        if i%16==15:
            print
    print

class SecurityHandler:
    def __init__(self, file, d):
        self.d = d
        self.file = file
        self.key = self.get_rc4_key()

    def encrypt(self, data, id):
        return self.decrypt(data, id) # rc4 is symmetric

    def decrypt(self, data, id):
        key = md5.md5("%s%c%c%c%c%c" % (self.key, 
                id.id&0xff, id.id>>8&0xff, id.id>>16&0xff,
                id.gen&0xff, id.gen>>8&0xff)).digest()
        key = key[:len(self.key)+5]
        key = key[:16]
        return rc4(data, key)

    def get_rc4_key(self):
        s = pad # (or user password)
        s += self.d["/O"].raw()[0:32] # owner key (should be 32 characters)
        p = self.d["/P"]
        s += chr(p&0xff)
        s += chr(p>>8&0xff)
        s += chr(p>>16&0xff)
        s += chr(p>>24&0xff)
        s += self.file.file_id[0].raw()
        encryptmetadata = self.d.get("/EncryptMetadata","true").lower() == "true"
        revision = int(self.d.get("/R", 1))
        if not encryptmetadata:
            s += "\xff\xff\xff\xff"
        try:
            keylen = int(self.d["/Length"]) / 8
        except:
            if revision==3:
                keylen = 16
            else:
                keylen = 5
        filekey = md5.md5(s).digest()[0:keylen]
        
        if revision==3:
            for i in range(50):
                filekey = md5.md5(filekey).digest()[0:keylen]
       
        userkey = self.d["/U"].raw()
        if revision==2:
            userkey = rc4(userkey, filekey)
            check(userkey == pad, "Invalid password")
        elif revision==3:
            for i in range(20).__reversed__():
                key = ""
                for c in filekey:
                    key += chr(ord(c)^i)
                userkey = rc4(userkey, key)
            pad_md5 = md5.md5(pad + self.file.file_id[0].raw()).digest()
            check(userkey[0:16] == pad_md5[0:16], "invalid password (3)")
        return filekey

class Reader:
    def __init__(self, file, bytes, pos=0):
        self.bytes = bytes
        self.pos = pos
        self.file = file
    
    def skip_whitespace(self):
        while self.pos<len(self.bytes):
            if self.bytes[self.pos]=='%':
                while self.bytes[self.pos] not in "\n\r" and self.pos < len(self.bytes):
                    self.pos = self.pos + 1
            if WHITESPACE.match(self.bytes[self.pos]) and self.pos < len(self.bytes):
                self.pos = self.pos+1
                continue
            else:
                break
        return self.pos
    
    def read_symbol(self):
        self.skip_whitespace()
        if self.pos == len(self.bytes):
            error("unexpected end of stream")
        start = self.pos
        m = TOKEN.match(self.bytes[self.pos:self.pos+256])
        if not m:
            error("invalid token: " + repr(self.bytes[self.pos:self.pos+64]))
        self.pos = self.pos+m.end()
        return self.bytes[start:self.pos]

    def peek_symbol(self):
        oldpos = self.pos
        s = self.read_symbol()
        self.pos = oldpos
        return s
    
    def read_dict(self):
        start = self.pos
        start1= self.pos
        token = self.read_symbol()
        check(token=="<<", "dict doesn't start with <<: "+repr(self.bytes[start:start+20]))
        d = {}
        while 1:
            start = self.pos
            s = self.peek_symbol()
            if s == ">>":
                self.read_symbol()
                break
            key = self.read_token()
            check(type(key)==str, "bad key: "+repr(self.bytes[start:start+30]))
            check(key.startswith("/"), "bad key: "+repr(self.bytes[start:start+30]))
            value = self.read_token()
            d[key] = value
        return PDFDict(d)

    def read_array(self):
        token = self.read_symbol()
        check(token=="[", "array doesn't start with [")
        a = []
        while 1:
            s = self.peek_symbol()
            if s == "]":
                self.read_symbol()
                break
            v = self.read_token()
            a += [v]
        return PDFArray(a)
    
    def read_id(self):
        check(self.bytes[self.pos]=='<',"ids must start with <")
        start = self.pos
        self.pos += 1
        num = 0
        c = 0
        result = []
        while self.bytes[self.pos] in "abcdefABCDEF0123456789"+WHITESPACECHARS:
            if self.bytes[self.pos] in WHITESPACECHARS:
                self.pos += 1
                continue
            num = num<<4|int(self.bytes[self.pos],16)
            self.pos += 1
            c += 1
            if c == 2:
                c = 0
                result.append(num)
                num = 0
        self.skip_whitespace()
        check(self.bytes[self.pos]=='>',"ids must end with >: %s" % repr(self.bytes[self.pos:self.pos+16]))
        self.pos += 1
        return PDFHexID(result)

    def read_stream(self,length):
        self.skip_whitespace()
        if self.bytes[self.pos:self.pos+6]=="stream":
            self.pos += 6
        else:
            error("Stream doesn't start with keyword 'stream'")
        if self.bytes[self.pos:self.pos+2] == "\r\n":
            self.pos += 2
        elif self.bytes[self.pos] == "\n":
            self.pos += 1
        elif self.bytes[self.pos] == "\r":
            # spec says this is illegal.
            warnings.warn("Stream keyword (at %d) is followed by a single carriage return" % (self.pos-6))
            self.pos += 1
        else:
            # and so is this. but there is always some program which doesn't care
            warnings.warn("No newline after 'stream' keyword at pos %d" % (self.pos-6))

        if not length:
            newpos = self.bytes.find("endstream", self.pos)
            return self.bytes[self.pos:newpos]
        else:
            start = self.pos
            self.pos += length
            self.skip_whitespace()
            if not self.bytes[self.pos:self.pos+9]=="endstream":
                extra_bytes = self.bytes.find("endstream",self.pos)
                if extra_bytes>=0:
                    # some pdf applications measure the stream size incorrectly
                    warnings.warn("incorrect stream length (off by %d bytes at pos %d)" % (extra_bytes-self.pos, self.pos))
                    self.pos += extra_bytes
                else:
                    error("Stream (%d bytes) at %d doesn't end with an 'endstream'" % (length, start))
            return self.bytes[start:start+length]

    def read_object(self, default_type=None):
        start = self.pos
        id1 = self.read_symbol()
        id2 = self.read_symbol()
        obj = self.read_symbol()
        check(id1.isdigit(), "object doesn't start with <<id>> <gen> obj, at %d" % start)
        check(id2.isdigit(), "object doesn't start with <id> <<gen>> obj, at %d" % start)
        check(obj=="obj", "object doesn't start with <id> <gen> obj, at %d" % start)
        id = ID(id1,id2)
        try:
            oldpos = self.pos
            d = self.read_dict()
        except PDFMalformedException:
            self.pos = oldpos
            d = self.read_token()
            if d == "endobj": # empty object
                next = d
                d = PDFDict({})
            else:
                next = self.read_symbol()
            check(next=="endobj", "streams only supported in 'dict' objects at %d" % self.pos)
            return PDFObject(self.file,id,d)

        length = 0
        if "/Length" in d and type(d["/Length"])==int:
            length = d["/Length"]
            # TODO: look up indirect objects?

        next = self.peek_symbol()
        stream = None
        self.skip_whitespace()
        if self.bytes[self.pos:self.pos+6]=="stream":
            stream = self.read_stream(length)
        elif self.bytes[self.pos:self.pos+6]=="endobj":
            self.pos+=6
            stream = ""
        elif OBJ_START.match(self.bytes[self.pos:self.pos+16]) or XREF_START.match(self.bytes[self.pos:self.pos+8]):
            # some buggy pdf files omit the endobj
            stream = ""
        else:
            #error("invalid end of object: "+repr(next))
            error("invalid end of object: "+self.bytes[self.pos:self.pos+6])
        return upcast(PDFDictObject(self.file,id,d,stream,self.file.encrypt!=None),default_type)
    
    def read_literal_string(self):
        start = self.pos
        o = 0
        s = 0
        while self.pos < len(self.bytes):
            if not s and self.bytes[self.pos] == '(':
                o += 1
                self.pos += 1
            elif not s and self.bytes[self.pos] == ')':
                o -= 1
                self.pos += 1
                if not o:
                    break
            elif s and self.bytes[self.pos] == '\\':
                s = 0
                self.pos += 1
            else:
                s = self.bytes[self.pos]=='\\'
                self.pos += 1
        end = self.pos
        # skip trailing )s- I've seen this in Acrobat PDFWriter 3.02 files
        while self.pos < len(self.bytes) and \
                self.bytes[self.pos] in (")"+WHITESPACECHARS):
            self.pos = self.pos+1
        return PDFString(self.bytes[start:end])

    def read_int(self):
        self.skip_whitespace()
        start = self.pos
        t = self.read_symbol()
        try:
            return int(t)
        except ValueError:
            error("not an integer: "+str(t))

    def read_float(self):
        self.skip_whitespace()
        t = self.read_symbol()
        try:
            return float(t)
        except ValueError:
            error("not a float: "+str(t))

    def read_token(self):
        self.skip_whitespace()
        first = self.peek_symbol()
        if first.startswith("<<"):
            t = self.read_dict()
        elif first.startswith("["):
            t = self.read_array()
        elif first.startswith("("):
            t = self.read_literal_string()
        elif first.startswith("<"):
            t = self.read_id()
        elif REF.match(first):
            id = re.split(WHITESPACE, self.read_symbol())
            t = ID(id[0],id[1])
        elif first[0] in "]>":
            error("bad delimiter '"+first[0]+"'")
        elif NUMBER.match(first):
            t = self.read_float()
        elif INTEGER.match(first):
            t = self.read_int()
        else:
            t = self.read_symbol()
        return t

python_identifier = id

class RawPDF(object):
    def __init__(self, data=None):
        self.id2object_cache = {}
        self.xref = {}
        self._next_object_id = 0
        self.encrypt = None
        self.version = (1,4)

        if data is not None:
            self.bytes = data
            self.read_root()
        else:
            self.root = self.create_object()
            self.root.d["/Type"] = "/Catalog"
            self.info = None
            self.encrypt = None
            self.file_id = None
            
        #self.verify_xref()

    def error(self, s):
        raise PDFMalformedException(s)
    
    def objects(self):
        for id,pos in self.xref.items():
            if not pos:
                # deleted
                continue
            yield self.read_object(pos,id)
    
    def dict_objects(self):
        return [o for o in self.objects() if isinstance(o,PDFDict)]
    
    def objects_of_type(self, t):
        return [o for o in self.objects() if isinstance(o,PDFDict) and o.get("/Type",None) == t]
    
    def objects_of_subtype(self, t):
        return [o for o in self.objects() if isinstance(o,PDFDict) and o.get("/Subtype",None) == t]
    
    def images(self):
        return self.objects_of_subtype("/Image")

    def actions(self):
        return self.objects_of_type("/Action")

    def check(self, b, s):
        if not b: 
            self.error(s)

    def next_id(self):
        self._next_object_id += 1
        return self._next_object_id

    def create_object(self, type=None, subtype=None, constructor=PDFDictObject):
        id = ID(self.next_id(),0)
        self.xref[id] = 0
        o = constructor(self,id,{},"",False)
        self.id2object_cache[id] = o
        if type:
            o.d["/Type"] = type
        if subtype:
            o.d["/Subtype"] = subtype
        self.changed(o)
        return o

    # overridden e.g. by PDFPatcher
    def changed(self, obj):
        pass

    def add_object(self, obj):
        id = ID(self.next_id(),0)
        obj.id = id
        self.xref[id] = 0
        self.id2object_cache[id] = obj
        self.changed(obj)

    def add_page(self, width, height, xpos=0, ypos=0):
        page = self.create_object("/Page", constructor=PDFPage)
        page.d["/MediaBox"] = PDFArray([xpos, ypos, xpos+width, ypos+height])
        page_content = page.d["/Contents"] = self.create_object()
        if not self.pages:
            self.pageparent = self.create_object()
            self.pageparent.d["/Type"] = "/Pages"
            self.root.d["/Pages"] = str(self.pageparent)
            self.pageparent.d["/Kids"] = PDFArray([])
        page.d["/Parent"] = self.pageparent
        self.pageparent.d["/Kids"].append(page)
        self.pageparent.d["/Count"] = self.pageparent.d.get("/Count",0)+1
        self.pages += [page]
        page_content.header = page
        return page_content

    def read_root(self):
        self.offset = 0
        if(self.bytes[0:4] != "%PDF-"):
            i = self.bytes.find("%PDF-")
            if i<0:
                self.error("no PDF header")
            else:
                self.offset = i
        version = self.bytes[self.offset+5:self.offset+8]
        self.check(version[1]==".", "bad version "+str(version))
        self.version = int(version[0]),int(version[2])

        rootid = None
        infoid = None
        encryptid = None
        file_id = None
        pos = self.parse_eof()
        while 1:
            pos = self.parse_xref(pos)
            trailer = self.parse_trailer(pos)
            if not rootid:
                if "/Root" not in trailer:
                    self.error("no root object")
                rootid = rootid or trailer["/Root"]
                infoid = infoid or trailer.get("/Info",None)
                encryptid = encryptid or trailer.get("/Encrypt",None)
                file_id = file_id or trailer.get("/ID",None)
            if "/Prev" not in trailer:
                break
            pos = trailer["/Prev"]+self.offset
            if self.xref_type == "broken":
                break
        
        self.root = self.get_object(rootid)
        self.info = None
        self.encrypt = None
        self.file_id = None
        if infoid:
            self.info = self.get_object(infoid)
        if file_id:
            self.file_id = file_id
        if encryptid and isinstance(encryptid,ID) or isinstance(encryptid,PDFDict):
            self.encrypt = self.get_object(encryptid)
            self.securityhandler = SecurityHandler(self, self.encrypt)

    def verify_xref(self):
        for id,pos in self.xref.items():
            if not pos:
                # deleted
                continue
            obj = self.bytes.find("obj", pos)
            err = ("bad object %s at pos %d: " % (str(id),pos))+repr(self.bytes[pos:pos+20])
            self.check(0 < obj-pos < 20, err)
            s = self.bytes[pos:obj].strip()
            version = re.split(WHITESPACE,s)
            self.check(len(version)==2, err)
            try: id2 = ID(int(version[0]),int(version[1]))
            except ValueError: self.error(err)
            if id!=id2:
                warnings.warn("object %s doesn't match object %s at pos %d" % (id, id2, pos))

    def get_object(self,object_or_id,default_type=None):
        if isinstance(object_or_id, PDFType):
            obj = object_or_id
            return obj
        else:
            id = object_or_id
            o = self.id2object_cache.get(id, None)
            if not o:
                o = self.read_object(self.xref[id],id,default_type)
                self.id2object_cache[id] = o
            return o

    def read_object(self,pos,id=None,default_type=None):
        if isinstance(pos, PDFObjectStreamReference):
            obj = self.get_object(pos.id)
            obj.decompress()
            self.check(("/N" in obj) and ("/First" in obj), "object stream without /N and /First")
            n = obj["/N"]
            r = Reader(self,obj.stream)
            # XXX: we don't use pos.id at all?
            for i in range(n):
                nr = r.read_int()
                offset = r.read_int()
                if nr == id.id:
                    d = Reader(self,obj.stream, offset+obj["/First"]).read_token()
                    if isinstance(d,PDFDict) or type(d)==dict:
                        return upcast(PDFDictObject(self,id,d,"",self.encrypt!=None),default_type)
                    else:
                        return PDFObject(self,id,d)
            self.error("indirect object %d not in object stream" % pos.id.id)
        else:
            return Reader(self,self.bytes,pos).read_object(default_type)

    def parse_eof(self):
        eof = self.bytes.rfind("%%EOF")
        self.check(eof>=0, "no EOF marker")
        startxref = self.bytes.rfind("startxref", 0, eof)
        if startxref<0:
            warnings.warn("no startxref at end of file")
            pos = self.bytes.rfind("xref")
            # for files I encountered so far that had a missing startxref,
            # the xref table itself was broken, too. We would need to
            # reconstruct it to make sense out of the file.
            return -1
        else:
            try: pos = int(self.bytes[startxref+9:eof].strip()) + self.offset
            except ValueError: 
                warnings.warn("bad xref location")
                return -1
            if pos > startxref:
                warnings.warn("xref table (%d) *after* startxref (%d)" % (pos, startxref))
                return -1
        self.check(pos>=0, "no xref table")
        self.first_xref_pos = pos
        return pos

    def reconstruct_xref(self):
        self.xref = {}
        for match in OBJ.finditer(self.bytes):
            pos = match.start()
            obj_id = int(match.group(1))
            gen = int(match.group(2))
            id = ID(obj_id,gen)
            self.xref[id] = pos
            self._next_object_id = max(self._next_object_id, id.id)

        for match in TRAILER.finditer(self.bytes):
            pos = match.start()+7
            d = Reader(self,self.bytes,pos).read_dict()
            if "/Root" in d:
                return pos
        self.error("no trailer")

    def parse_old_xref(self, pos):
        if self.bytes[pos]!="x":
            o = 0
            if self.bytes[pos]=="r": o=-1 # startxref off by one
            elif self.bytes[pos]=="e": o=-2 # startxref off by two
            elif self.bytes[pos]=="f": o=-3 # startxref off by three
            elif pos<len(self.bytes)-1 and self.bytes[pos+1]=="x": o=1 # startxref off by one (negative)
            elif pos<len(self.bytes)-2 and self.bytes[pos+2]=="x": o=2 # startxref off by one (negative)
            elif pos<len(self.bytes)-3 and self.bytes[pos+3]=="x": o=3 # startxref off by one (negative)
            self.offset += o
            pos += o

        trailer = self.bytes.find("trailer", pos)
        if trailer<0:
            self.error("no trailer found")
        self.check(0<trailer, "no xref trailer")
        lines = [line.strip() for line in re.split(NEWLINE, self.bytes[pos:trailer]) if line.strip()]
        
        self.check(lines[0].startswith("xref"), "xref table doesn't start with an xref: "+repr(lines[0]))
        if INLINE_XREF.match(lines[0]):
            lines[0] = lines[0][4:].strip()
            j = 0
        else:
            j = 1
        while j < len(lines):
            entries = re.split(WHITESPACE, lines[j])
            self.check(len(entries)==2, "broken xref header: "+repr(lines[j]))
            j += 1
            try: start,num = int(entries[0]),int(entries[1])
            except ValueError: self.error("broken xref header.")
            self.check(len(lines)-1 >= num, "too few xref entries")
            for i in range(num):
                entries = re.split(WHITESPACE, lines[j])
                j += 1
                self.check(len(entries)==3, "malformed xref entry")
                try: 
                    offset = int(entries[0])
                    gen = int(entries[1])
                except ValueError: self.error("bad xref location entry")
                flag = entries[2]
                if start+i and flag!="f" and offset:
                    id = ID(start+i,gen)
                    # we read backwards, so ids read first overload ids read later
                    if id not in self.xref:
                        self.xref[id] = offset+self.offset
                        self._next_object_id = max(self._next_object_id, id.id)
        return trailer+7

    def parse_new_xref(self,pos):
        obj = self.read_object(pos)
        obj.decompress()
        if "/Index" in obj:
            index = obj["/Index"]
        else:
            index = [0, obj["/Size"]]
        if len(index)%2!=0:
            self.error("/Index array must be multiple of two")
        self.check("/W" in obj, "xref object has no /W attribute")

        w = obj["/W"]
        data = obj.stream
        pos = 0
        #print repr(data[0:32])
        for start, size in zip(index[0:len(index):2],index[1:len(index):2]):
            for i in range(size):
                t = None
                gen = 0
                row = []
                for size in w:
                    val = 0
                    for c in range(size):
                        val = val<<8 | ord(data[pos])
                        pos += 1
                    row += [val]
                id = None
                if row[0]==1:
                    offset = row[1]
                    id = ID(start+i,row[2])
                elif row[0]==2:
                    id = ID(start+i,0)
                    offset = PDFObjectStreamReference(ID(row[1],0),row[2])

                if id and id not in self.xref:
                    self.xref[id] = offset
                    self._next_object_id = max(self._next_object_id, id.id)
        return obj.d

    def verify_xref(self):
        for key,pos in self.xref.items():
            if isinstance(pos,PDFObjectStreamReference):
                # this is actually the position of the containing object-
                # but we're not going to decompress object streams just
                # to check the xref table.
                pos = self.xref[pos.id]
            # notice that we don't check that each xref points to the *right* object.
            # we're a bit forgiving that way- if a pdf has xref entries that point to
            # older versions of objects, those objects probably aren't used/referenced,
            # anyway.
            if not OBJ_START.match(self.bytes[pos:pos+32]):
                self.error("Object %s doesn't start with 'obj'" % str(key))

    def parse_xref(self, pos):
        try:
            if pos>=0 and OBJ_START.match(self.bytes[pos:pos+32]):
                # pdf 1.6 object-style xref
                self.xref_type = "new"
                ret = self.parse_new_xref(pos)
            elif pos>=0 and XREF.match(self.bytes[pos:pos+32]):
                self.xref_type = "old"
                ret = self.parse_old_xref(pos)
            else:
                raise PDFMalformedException("bad startxref pointer")
            self.verify_xref()
            return ret
        except PDFMalformedException:
            warnings.warn("Reconstructing xref table")
            self.xref_type = "broken"
            return self.reconstruct_xref()

    def parse_trailer(self, trailerinfo):
        if isinstance(trailerinfo, PDFDict) or type(trailerinfo)==dict:
            # new style xref already included the trailer
            trailer = trailerinfo
        elif type(trailerinfo)==int:
            trailer = Reader(self,self.bytes, trailerinfo).read_dict()
        else:
            self.error("internal error")
            return
        #self.check("/Encrypt" not in trailer, "Document is encrypted")
        return trailer

    def decompress(self):
        for id in self.xref:
            obj = self.get_object(id)
            obj.decompress()

    def wipe_object(self, o):
        del self.id2object_cache[o.id]
        del self.xref[o.id]
        
    def wipe_objects(self, objects):
        for o in objects:
            self.wipe_object(o)

    def make_trailer_dict(self, previous=None):
        d = PDFDict({})
        d["/Root"] = self.root.id
        d["/Size"] = self._next_object_id
        if previous is not None:
            d["/Prev"] = previous
        if self.encrypt:
            d["/Encrypt"] = self.encrypt
        if self.info:
            d["/Info"] = self.info
        if self.file_id:
            d["/ID"] = self.file_id
        return d

    def write(self, fi):
        def w(s):
            fi.write(s.encode("latin-1"))
        objects = []
        w("%%PDF-%d.%d\n" % self.version)
        w("%\xea\x7e\n")
        xref = "xref\n"
        s,end = 0,self._next_object_id
        while s<end:
            size = s
            next = s
            while size<end:
                id = ID(size,0)
                if size and id not in self.xref:
                    next = size+1
                    break
                size += 1
            else:
                next = size

            xref += "%d %d\n" % (s,size-s)
            for nr in range(s,size):
                id = ID(nr,0) 
                exists = 0
                if id in self.xref:
                    obj = self.get_object(id)
                    if obj.get_type() not in ["/XRef","/ObjStm"]:
                        pos = fi.tell()
                        w(obj.obj())
                        xref += "%010d %05d n \n" % (pos,obj.id.gen)
                        exists = 1
                if not exists:
                    if nr==0:
                        xref += "0000000000 65535 f \n"
                    else:
                        xref += "0000000000 65536 n \n"
            s = next

        xref_pos = fi.tell()
        w(xref)
        w("trailer\n")
        d = self.make_trailer_dict()
        w(str(d))
        w("startxref\n%d\n%%%%EOF" % xref_pos)

    def __str__(self):
        return "<RawPDF %d objects>" % len(self.objects)


class PDF(RawPDF):
    def __init__(self, data=None):
        super(PDF,self).__init__(data)
        self.pages = self.parse_pages_and_cleanup()

    @staticmethod
    def load(filename):
        with open(filename, "rb") as fi:
            return PDF(str(fi.read(), "latin-1"))

    def parse_pages_and_cleanup(self):
        self.check("/Pages" in self.root, "no /Pages reference in /Root")
        page_objects=[]
        pages = self.parse_page(self.root["/Pages"], None, page_objects)
        self.wipe_objects(page_objects)
        for page in pages:
            page.id = None
            del page["/Parent"]
        return pages

    def parse_page(self, id, parent, objects=[]):
        page = self.get_object(id, default_type="/Page")
        objects.append(page)
        if "/Kids" in page:
            o = page["/Kids"]
            if isinstance(o, ID):
                o = self.get_object(o).d
                objects.append(o)
            pages = []
            for child in o:
                try:
                    pages += self.parse_page(child, page, objects)
                except KeyError as e:
                    warnings.warn("couldn't read page")
            return pages
        else:
            page.parent = parent
            return [page]

    def __str__(self):
        return "<PDF, %d pages>" % len(self.pages)

    def write(self, fi):
        p = self.create_object("/Pages")
        p["/Count"] = len(self.pages)
        p["/Kids"] = PDFArray(self.pages)
        for page in self.pages:
            self.add_object(page)
            page["/Parent"] = p.id
        self.root["/Pages"] = p
        self.changed(self.root)
        super(PDF, self).write(fi)

    def save(self, filename):
        with open(filename, "wb") as fi:
            self.write(fi)

if __name__ == "__main__":
    p = PDF.load("test.pdf")
    p.decompress()
    p.save("out.pdf")

