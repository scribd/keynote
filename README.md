About:
------

This is the open source version of Scribd's keynote converter. 
It came into existence during the 2013 Scribd hackday and was open-sourced later that year.

It supports Apple's keynote format for `.key` files until, at least, Mac OS X Mountain Lion.

Several features (like body placeholders, spiral shapes, certain arrows and some types of bullet
point lists) are not yet supported.

Requirements:
-------------
* Python 3
* pycairo (for PDF generation)
* numpy
* pillow
* fontconfig, freetype (non Python libraries, imported via ctypes)
* poppler (for rendering embedded PDFs)

Usage:
------

Run 
```shell
    key2pdf file.key -o file.pdf
```
to convert a keynote file to PDF.

