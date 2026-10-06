import unittest

from app.services.perimeter_resolver import (
    resolve_perimeter_type_for_event_profile,
)


class DummyEventProfile:
    def __init__(self, state_prov: str | None, country: str | None):
        self.state_prov = state_prov
        self.country = country


class PerimeterResolverTests(unittest.TestCase):
    def test_texas_resolves_to_andymark(self):
        profile = DummyEventProfile(state_prov="TX", country="USA")
        perimeter_type, source = resolve_perimeter_type_for_event_profile(profile)
        self.assertEqual(perimeter_type, "andymark")
        self.assertEqual(source, "state:tx")

    def test_canada_resolves_to_welded(self):
        profile = DummyEventProfile(state_prov="ON", country="Canada")
        perimeter_type, source = resolve_perimeter_type_for_event_profile(profile)
        self.assertEqual(perimeter_type, "welded")
        self.assertEqual(source, "country:canada")

    def test_unknown_profile_defaults_to_welded(self):
        perimeter_type, source = resolve_perimeter_type_for_event_profile(None)
        self.assertEqual(perimeter_type, "welded")
        self.assertEqual(source, "default:no_event_profile")


if __name__ == "__main__":
    unittest.main()
