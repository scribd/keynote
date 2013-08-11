import os
import sys
basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(basedir)
sys.path.append(os.path.join(basedir, "keynote"))
import unittest
from unittest import TestCase
import zipfile
from keynote.xml import new_xml
from keynote.keynote import Keynote
from keynote.pdf import PDF

def add_geometry(e, w, h):
    e.sf_geometry.sf_size(sfa_w=w, sfa_h=h)
    e.sf_geometry.sf_position(sfa_x=0, sfa_y=0)
    e.sf_geometry.sf_naturalSize(sfa_w=0, sfa_h=0)

class KeynoteTest(TestCase):
    WIDTH = 800
    HEIGHT = 600
    def setUp(self):
        xml = new_xml()
        p = xml.key_presentation(sfa_ID="Key-0", key_version="92008102400")
        p.key_size(sfa_w=self.WIDTH, sfa_h=self.HEIGHT)
        p.top_level_styles
        slides = xml.key_presentation.key_slide_list
        slide = slides.key_slide
        slide.key_stylesheet.sf_slide_style.sf_fill.sf_color(sfa_w="0.0", sfa_a="0.0")
        slide.key_page.sf_drawables
        
        self.xml = xml
        self.slide = slide # first slide

    def convert(self, extra_files=[]):
        z = zipfile.ZipFile("_test.key", "w")
        z.writestr("index.apxl", str(self.xml))
        for filename in extra_files:
            z.write(os.path.join("tests/files/",filename), filename)
        z.close()
        k = Keynote("_test.key")
        k.save("_test.pdf")
        return PDF.load("_test.pdf")

    def test_empty(self):
        pdf = self.convert()
        self.assertEqual(list(pdf.pages[0]["MediaBox"]), [0,0,self.WIDTH,self.HEIGHT])

    def test_images(self):
        xml = self.xml

        media = self.slide.key_page.sf_drawables.sf_media
        add_geometry(media, 512, 512)
        unfiltered = media.sf_content.sf_image_media.sf_filtered_image.sf_unfiltered
        unfiltered.sf_size(sfa_w="400", sfa_h="400")
        unfiltered.sf_data(sf_path="baboon.png")

        pdf = self.convert(extra_files=["baboon.png"])
        self.assertEqual(pdf.images()[0]["Width"], 512)
        self.assertEqual(pdf.images()[0]["Height"], 512)

if __name__ == "__main__":
    unittest.main()

