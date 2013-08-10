from lxml import etree
from . import utils

SFA = "{http://developer.apple.com/namespaces/sfa}"
SF = "{http://developer.apple.com/namespaces/sf}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
KEY = "{http://developer.apple.com/namespaces/keynote2}"

NAMESPACE_TO_URL = {
    "sfa": SFA,
    "sf": SF,
    "xsi": XSI,
    "key": KEY,
}

URL_TO_NAMESPACE = utils.invert_dict(NAMESPACE_TO_URL)

def ns(qname):
    """ returns the lxml representation of an xml namespace,
        using a static lookup table. """
    if len(qname) and qname[0] == "{":
        return qname
    i = qname.find(":")
    if i<0:
        return qname
    return NAMESPACE_TO_URL[qname[0:i]] + qname[i+1:]

class XMLError(Exception):
    pass

class Element(object):
    """ Keynote specific Wrapper for an lxml element. Provides automatic 
        namespace lookup and handling of IDs and IDREFs. """

    registry = {}
    sfa_ID = ns("sfa:ID")
    sfa_IDREF = ns("sfa:IDREF")

    def __init__(self, e):
        self.e = e

    @property
    def tag(self):
        return self.e.tag

    @property
    def shorttag(self):
        name = self.e.tag
        i = name.find("}")
        if i>=0:
            ns = name[0:i+1]
            return URL_TO_NAMESPACE[ns]+":"+name[i+1:]
        return name

    @property
    def text(self):
        return self.e.text

    @property
    def tail(self):
        return self.e.tail

    def get(self, name, default=None):
        return self.e.get(ns(name), default)

    def find(self, name):
        #FIXME: remove all usages of ns()
        result = self.e.find(ns(name))
        if result is None:
            return None
        return Element(result)

    def find_or_lookup(self, name):
        result = self.find(name)
        if result is not None:
            return result
        name = ns(name)
        ref = self.e.find(name+"-ref")
        if ref is not None:
            ref_id = ref.get(Element.sfa_IDREF)
            if ref_id is None:
                raise XMLError(name+"-ref without sfa:IDREF")
            result = Element.registry.get(ref_id)
            if result is None:
                raise XMLError("Couldn't find IDREF %s in XML" % ref_id)
            return Element(result)

    def findall(self, name):
        result = self.e.findall(ns(name))
        return (Element(e) for e in result)

    def iter(self, name=None):
        if name is None:
            return (Element(e) for e in self.e.iter())
        else:
            name = ns(name)
            return (Element(e) for e in self.e.iter(name))

    def getparent(self):
        p = self.e.getparent()
        if p is None:
            return None
        return Element(p)

    def has_parent(self, name):
        p = self.e.getparent()
        while p is not None:
            if p.tag == name:
                return True
            p = p.getparent()
        return False

    def __iter__(self):
        return (Element(e) for e in self.e)

    def resolve_in_place(self):
        if self.e.tag.endswith("-ref"):
            ref_id = self.e.get(Element.sfa_IDREF)
            self.e = Element.registry[ref_id]
        return self

    def resolve(self):
        return Element(self.e).resolve_in_place()

    def lookup_children(self):
        return (Element(e).resolve_in_place() for e in self.e)

    def iter_with_lookup(self, name=None):
        queue = [self.e]
        while len(queue) > 0:
            e = queue.pop()
            if e.tag.endswith("-ref"):
                e = Element.registry[e.get(Element.sfa_IDREF)]
            if name is None or e.tag == name:
                yield Element(e)
            for c in e:
                queue.append(c)

    def __str__(self):
        return etree.tostring(self.e, pretty_print=True, xml_declaration=True, encoding="utf-8")

    def __len__(self):
        return len(self.e)

    def __getitem__(self, pos):
        return Element(self.e[pos])

    @staticmethod
    def fill_registry(xml):
        for element in xml.iter():
            id = element.get(Element.sfa_ID)
            if id is not None:
                Element.registry[id] = element

def XML(data):
    root = etree.XML(data)
    Element.fill_registry(root)
    return Element(root)

