"""Study runners and scenario provenance.

Only part of the scenario generator ships in this repository:

- ``pref_study.py`` and ``explain_study.py`` are the runners behind the paper's numbers
  (driven by ``experiments/run.py``), and ``runinfo.py`` is their provenance helper;
- ``source.py`` is the pydantic shape of a scenario's generator provenance, which
  ``scenarios/registry.py`` reads when a ``<slug>.source.json`` sits beside a curated
  scenario (none do here).

The generator itself, which sampled the scenarios, is not included.
"""
