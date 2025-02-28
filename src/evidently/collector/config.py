import abc
import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import pandas as pd
from pydantic.v1 import BaseModel
from pydantic.v1 import parse_obj_as

from evidently.base_metric import Metric
from evidently.collector.storage import CollectorStorage
from evidently.collector.storage import InMemoryStorage
from evidently.options.base import Options
from evidently.pydantic_utils import PolymorphicModel
from evidently.report import Report
from evidently.suite.base_suite import MetadataValueType
from evidently.test_suite import TestSuite
from evidently.tests.base_test import Test
from evidently.ui.remote import RemoteWorkspace
from evidently.utils import NumpyEncoder

CONFIG_PATH = "collector_config.json"


class Config(BaseModel):
    @classmethod
    def load(cls, path: str):
        with open(path) as f:
            return parse_obj_as(cls, json.load(f))

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.dict(), f, cls=NumpyEncoder, indent=2)


class CollectorTrigger(PolymorphicModel):
    @abc.abstractmethod
    def is_ready(self, config: "CollectorConfig", storage: "CollectorStorage") -> bool:
        raise NotImplementedError


class IntervalTrigger(CollectorTrigger):
    interval: float
    last_triggered: float = 0

    def is_ready(self, config: "CollectorConfig", storage: "CollectorStorage") -> bool:
        now = time.time()
        if now - self.last_triggered > self.interval:
            self.last_triggered = now
            return True
        return False


class ReportConfig(Config):
    metrics: List[Metric]
    tests: List[Test]
    options: Options
    metadata: Dict[str, MetadataValueType]
    tags: List[str]

    @classmethod
    def from_report(cls, report: Report):
        return ReportConfig(
            metrics=report._first_level_metrics,
            tests=[],
            options=report.options,
            metadata=report.metadata,
            tags=report.tags,
        )

    @classmethod
    def from_test_suite(cls, test_suite: TestSuite):
        return ReportConfig(
            tests=test_suite._inner_suite.context.tests,
            metrics=[],
            options=test_suite.options,
            metadata=test_suite.metadata,
            tags=test_suite.tags,
        )

    def to_report_base(self) -> Union[TestSuite, Report]:
        if len(self.tests) > 0:
            return TestSuite(
                tests=self.tests,  # type: ignore[arg-type]
                options=self.options,
                metadata=self.metadata,
                tags=self.tags,
            )
        return Report(
            metrics=self.metrics,  # type: ignore[arg-type]
            options=self.options,
            metadata=self.metadata,
            tags=self.tags,
        )


class CollectorConfig(Config):
    class Config:
        underscore_attrs_are_private = True

    id: str = ""
    trigger: CollectorTrigger
    report_config: ReportConfig
    reference_path: Optional[str]

    project_id: str
    api_url: str = "http://localhost:8000"
    api_secret: Optional[str] = None
    cache_reference: bool = True

    _reference: Any = None
    _workspace: Optional[RemoteWorkspace] = None

    @property
    def workspace(self) -> RemoteWorkspace:
        if self._workspace is None:
            self._workspace = RemoteWorkspace(base_url=self.api_url, secret=self.api_secret)
        return self._workspace

    def _read_reference(self):
        return pd.read_parquet(self.reference_path)

    @property
    def reference(self):
        if self.reference_path is None:
            return None
        if self._reference is not None:
            return self._reference
        if not self.cache_reference:
            return self._read_reference()
        self._reference = self._read_reference()
        return self._reference


class CollectorServiceConfig(Config):
    check_interval: float = 1
    collectors: Dict[str, CollectorConfig] = {}
    storage: CollectorStorage = InMemoryStorage()

    @classmethod
    def load_or_default(cls, path: str):
        try:
            return cls.load(path)
        except FileNotFoundError:
            default = CollectorServiceConfig()
            default.save(path)
            return default
