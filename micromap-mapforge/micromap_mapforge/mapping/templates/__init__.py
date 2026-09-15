# Marker so micromap_mapforge.mapping.templates is a recognized package
# directory and importlib.resources can enumerate its YAML files.
#
# VERSION DISCIPLINE (#152/#153): editing any template's body requires bumping
# its `version:` field (minor for additive, major for breaking) and regenerating
# the lock: `python -m micromap_mapforge.mapping.template_lock`. `versions.lock`
# pins {version, body-sha256} per template and the guard in
# `tests/mapping/test_template_versioning.py` fails CI if a body changed without a
# bump — so version numbers stay meaningful (and a future `templates diff` has a
# real artifact to point at).
