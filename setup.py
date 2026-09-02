# Copyright 2026 General Atomics
# Licensed under the Apache License, Version 2.0.

from setuptools import setup, find_packages
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import versioneer

setup(
    name="toksearch_cmf",
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    packages=find_packages(include=["toksearch_cmf", "toksearch_cmf.*"]),
    include_package_data=True,
    zip_safe=False,
)
