"""Kayak's speech server: one runtime, any model.

Runs inside its own image so that its dependencies -- torch and a growing set of
speech libraries that pin incompatible versions of it -- stay out of the Kayak
server, and so a model's own code executes in a container rather than in the app.
"""
