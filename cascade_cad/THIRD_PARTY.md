# Dependencies

The add-on source is licensed GPL-3.0-or-later; see LICENSE.txt.

The CAD runtime is installed separately and is not included in the extension ZIP.

- CadQuery 2.6.1: Apache-2.0. https://github.com/CadQuery/cadquery
- OCP (cadquery-ocp 7.8.1.1.post1): Python bindings for Open CASCADE. https://github.com/CadQuery/OCP
- Open CASCADE Technology: LGPL-2.1 with additional exception. https://www.occt3d.com/dev/doc/overview/html/index.html
- Python 3.12.10: PSF License. https://www.python.org/downloads/release/python-31210/

The installer fetches Python from python.org and Python packages from PyPI. Runtime dependency license notices are retained in installed packages and their distribution metadata. This project is independent of Plasticity, Siemens and Open Cascade SAS.

## Optional remesh engine (0.3.0)

Python C ABI declarations and solver configuration files in cascade_quadwild/ are adapted from ksami/QRemeshify 1.1.0, commit e5e567958b34cfaed373a102bb7aa56454d642aa, GPL-3.0-or-later. The upstream license is retained in cascade_quadwild/LICENSE.txt.

- QRemeshify: https://github.com/ksami/QRemeshify
- QuadWild (Pietroni et al., SIGGRAPH 2021): https://github.com/nicopietroni/quadwild
- QuadWild + Bi-MDF: https://github.com/cgg-bern/quadwild-bimdf

No native remesh DLL is redistributed in this extension or its repository. The optional installer retrieves the official Windows release separately and verifies the pinned archive and library SHA-256 hashes. The original source projects and license notices remain linked in the installer metadata.
