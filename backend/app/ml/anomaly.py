"""Anomaly flag: the CNN's own top confidence below the ACTIVE model's confidence_threshold
(model_versions row, default 0.75). There is no second model."""


class AnomalyDetector:
    @staticmethod
    def is_anomaly(top_confidence: float, threshold: float) -> bool:
        return top_confidence < threshold
