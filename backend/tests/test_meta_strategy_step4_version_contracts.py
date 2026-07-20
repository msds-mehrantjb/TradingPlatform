from __future__ import annotations

import ast
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_MANDATORY_VERSION_FIELDS,
    META_STRATEGY_VERSION_IDENTIFIER_ITEMS,
    MetaStrategyBoundaryManifest,
    MetaStrategyPersistedResultEnvelope,
    MetaStrategyVersionContract,
    build_current_behavior_characterization,
    meta_strategy_boundary_manifest,
    meta_strategy_configuration,
    meta_strategy_contract_inventory,
    meta_strategy_persisted_result_envelope,
    meta_strategy_version_compatibility,
    meta_strategy_version_contract,
    meta_strategy_version_identifiers,
    validate_boundary_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
VERSIONS_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "versions.py"


class MetaStrategyStep4VersionContractsTest(unittest.TestCase):
    maxDiff = None

    def test_version_identifiers_are_independent_immutable_constants(self) -> None:
        expected_fields = (
            "algorithmVersion",
            "strategyCatalogVersion",
            "featureSchemaVersion",
            "labelSpecificationVersion",
            "modelVersion",
            "modelArtifactVersion",
            "configurationVersion",
            "dynamicProfileVersion",
            "positionSizingVersion",
            "exitPolicyVersion",
            "backtestEngineVersion",
        )
        identifiers = meta_strategy_version_identifiers()
        values = list(identifiers.values())

        self.assertEqual(META_STRATEGY_MANDATORY_VERSION_FIELDS, expected_fields)
        self.assertEqual(tuple(identifiers), expected_fields)
        self.assertEqual(META_STRATEGY_VERSION_IDENTIFIER_ITEMS, tuple(identifiers.items()))
        self.assertEqual(len(values), len(set(values)))
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(value.startswith("meta_strategy_"))
                self.assertTrue(value.endswith("_v1"))

    def test_version_constants_are_declared_as_final_strings(self) -> None:
        version_constant_names = {
            "META_STRATEGY_ALGORITHM_VERSION",
            "META_STRATEGY_STRATEGY_CATALOG_VERSION",
            "META_STRATEGY_FEATURE_SCHEMA_VERSION",
            "META_STRATEGY_LABEL_SPECIFICATION_VERSION",
            "META_STRATEGY_MODEL_VERSION",
            "META_STRATEGY_MODEL_ARTIFACT_VERSION",
            "META_STRATEGY_CONFIGURATION_VERSION",
            "META_STRATEGY_DYNAMIC_PROFILE_VERSION",
            "META_STRATEGY_POSITION_SIZING_VERSION",
            "META_STRATEGY_EXIT_POLICY_VERSION",
            "META_STRATEGY_BACKTEST_ENGINE_VERSION",
        }
        tree = ast.parse(VERSIONS_PATH.read_text(encoding="utf-8"))
        annotations = {
            target.id: ast.unparse(node.annotation)
            for node in tree.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            for target in (node.target,)
        }

        self.assertTrue(version_constant_names.issubset(annotations))
        for constant_name in version_constant_names:
            with self.subTest(constant=constant_name):
                self.assertEqual(annotations[constant_name], "Final[str]")

    def test_current_version_contract_is_frozen_and_complete(self) -> None:
        contract = meta_strategy_version_contract()

        self.assertEqual(contract.model_dump(mode="json"), meta_strategy_version_identifiers())
        with self.assertRaises(ValidationError):
            MetaStrategyVersionContract()
        with self.assertRaises(ValidationError):
            MetaStrategyVersionContract(**{key: value for key, value in meta_strategy_version_identifiers().items() if key != "modelVersion"})
        with self.assertRaises(ValidationError):
            MetaStrategyVersionContract(**{**meta_strategy_version_identifiers(), "modelVersion": ""})
        with self.assertRaises(ValidationError):
            setattr(contract, "modelVersion", "meta_strategy_model_v2")
        with self.assertRaises(ValidationError):
            MetaStrategyVersionContract(**{**meta_strategy_version_identifiers(), "extraVersion": "not_allowed"})

    def test_boundary_manifest_rejects_missing_mandatory_versions(self) -> None:
        with self.assertRaises(ValidationError):
            MetaStrategyBoundaryManifest()
        with self.assertRaises(ValidationError):
            MetaStrategyBoundaryManifest(versions={})

        manifest = meta_strategy_boundary_manifest()
        self.assertTrue(validate_boundary_manifest(manifest)["valid"])
        self.assertEqual(manifest.versions, meta_strategy_version_contract())

    def test_persisted_result_envelopes_must_carry_versions(self) -> None:
        with self.assertRaises(ValidationError):
            MetaStrategyPersistedResultEnvelope(resultType="model_artifact")
        with self.assertRaises(ValidationError):
            MetaStrategyPersistedResultEnvelope(resultType="model_artifact", versions={})

        envelope = meta_strategy_persisted_result_envelope(
            result_type="model_artifact",
            payload={"artifactId": "fixture-artifact"},
        )

        self.assertEqual(envelope.algorithmId, "meta_strategy")
        self.assertEqual(envelope.versions, meta_strategy_version_contract())
        self.assertEqual(envelope.payload["artifactId"], "fixture-artifact")

    def test_version_compatibility_accepts_current_and_rejects_missing_or_mismatched(self) -> None:
        current = meta_strategy_version_contract()
        missing_model = {key: value for key, value in meta_strategy_version_identifiers().items() if key != "modelVersion"}
        mismatched_feature_schema = {**meta_strategy_version_identifiers(), "featureSchemaVersion": "meta_strategy_feature_schema_v0"}

        self.assertTrue(meta_strategy_version_compatibility(current)["valid"])
        self.assertTrue(meta_strategy_version_compatibility({"versions": current.model_dump(mode="json")})["valid"])
        self.assertFalse(meta_strategy_version_compatibility(missing_model)["valid"])
        self.assertEqual(meta_strategy_version_compatibility(missing_model)["missing"], ("modelVersion",))
        self.assertFalse(meta_strategy_version_compatibility(mismatched_feature_schema)["valid"])
        self.assertEqual(meta_strategy_version_compatibility(mismatched_feature_schema)["mismatched"], ("featureSchemaVersion",))

    def test_persisted_package_outputs_carry_versions(self) -> None:
        characterization = build_current_behavior_characterization()
        configuration = meta_strategy_configuration().baseline_configuration()
        inventory = meta_strategy_contract_inventory()

        self.assertTrue(meta_strategy_version_compatibility(characterization)["valid"])
        self.assertTrue(meta_strategy_version_compatibility(configuration)["valid"])
        self.assertTrue(meta_strategy_version_compatibility(inventory)["valid"])


if __name__ == "__main__":
    unittest.main()
