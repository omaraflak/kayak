"""Local model serving: one container per modality, each with its own lifecycle.

Deliberately imports nothing: the modules here import each other (a manager reads
the Metal control files, the registry builds managers), and re-exporting from the
package would make every one of those a cycle through this file.
"""
