"""Database layer: PostgreSQL (postgres.py), MongoDB (mongo.py), S3 (s3.py), table models
(models/), repositories (repositories/), Alembic migrations (migrations/) and seed data (seed.py).

Rule (BR-5.1): only repositories talk to a store. Services and routers never build SQL or Mongo
queries themselves.
"""
