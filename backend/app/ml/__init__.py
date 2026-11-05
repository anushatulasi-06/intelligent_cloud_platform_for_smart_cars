"""Inference: AudioPreprocessor (preprocessor.py), CnnClassifier (classifier.py), AnomalyDetector
(anomaly.py), SeverityPolicy (severity.py). The orchestrator that ties them together is
app/services/orchestrator.py.

All audio, feature and architecture parameters come from <MODEL_DIR>/<version>/config.json, which is
copied from the training code (model/config.py, model/cnn.py) when a model is saved. Nothing is
hard-coded here, so inference always matches training.
"""
