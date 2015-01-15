#!/usr/bin/env python
#-*- encoding: utf-8 -*-

"""
    Setup for Panthera buildZip application
"""
 
from distutils.core import setup 

setup(
    name='Worktime logging applicatioon',
    author='Damian Kęska',
    license = "LGPLv3",
    package_dir={'': 'src'},      
    packages=['liblogtime'],
    author_email='damian@pantheraframework.org',
    scripts=['logtime']
)
