"""
Informativlik hisobidagi sof matematik funksiyalar uchun unit testlar.

Ishga tushirish:
    python manage.py test app_informativlik --settings=seismo_project.settings_test
"""

from django.test import SimpleTestCase

from .views import merge_intervals, gauss_phi, compute_q_advanced


class MergeIntervalsTests(SimpleTestCase):
    def test_empty_list(self):
        self.assertEqual(merge_intervals([]), [])

    def test_single_interval(self):
        self.assertEqual(merge_intervals([(1, 5)]), [(1, 5)])

    def test_non_overlapping_kept_separate(self):
        self.assertEqual(merge_intervals([(1, 3), (5, 8)]), [(1, 3), (5, 8)])

    def test_overlapping_merged(self):
        self.assertEqual(merge_intervals([(1, 5), (3, 8)]), [(1, 8)])

    def test_touching_intervals_merged(self):
        # Chegara ustma-ust tushsa ham birlashadi
        self.assertEqual(merge_intervals([(1, 5), (5, 9)]), [(1, 9)])

    def test_unsorted_input_handled(self):
        self.assertEqual(merge_intervals([(6, 9), (1, 3), (2, 5)]), [(1, 5), (6, 9)])

    def test_contained_interval_absorbed(self):
        self.assertEqual(merge_intervals([(1, 10), (3, 5)]), [(1, 10)])


class GaussPhiTests(SimpleTestCase):
    def test_phi_at_zero_is_half(self):
        # Standart normal taqsimotda Φ(0) = 0.5
        self.assertAlmostEqual(gauss_phi(0), 0.5, places=6)

    def test_phi_is_monotonic(self):
        self.assertLess(gauss_phi(-1), gauss_phi(0))
        self.assertLess(gauss_phi(0), gauss_phi(1))

    def test_phi_known_value(self):
        # Φ(1.96) ≈ 0.975 (statistikadagi mashhur qiymat)
        self.assertAlmostEqual(gauss_phi(1.96), 0.975, places=3)

    def test_phi_bounds(self):
        self.assertGreaterEqual(gauss_phi(-10), 0.0)
        self.assertLessEqual(gauss_phi(10), 1.0)


class ComputeQAdvancedTests(SimpleTestCase):
    """compute_q_advanced (q, delta, mu) tuple qaytaradi."""

    def test_perfect_predictor(self):
        # Ko'pchilik zilzilalar tutilgan va anomaliya vaqti kichik (t << T):
        # q musbat bo'lishi kerak
        q, delta, mu = compute_q_advanced(m=9, n=10, t=30, T=3650)
        self.assertGreater(q, 0)

    def test_useless_predictor(self):
        # Hech narsa tutilmagan (m=0): q = 0 qaytadi (himoya sharti)
        q, delta, mu = compute_q_advanced(m=0, n=10, t=100, T=3650)
        self.assertEqual(q, 0.0)

    def test_more_captures_higher_q(self):
        # Boshqa hamma narsa teng bo'lsa, ko'proq tutish yuqoriroq q beradi
        q_low, _, _ = compute_q_advanced(m=2, n=10, t=100, T=3650)
        q_high, _, _ = compute_q_advanced(m=8, n=10, t=100, T=3650)
        self.assertGreater(q_high, q_low)

    def test_zero_division_guarded(self):
        # Nolga bo'linish holatlari xato bermasligi, (0,0,0) qaytishi kerak
        q, delta, mu = compute_q_advanced(m=0, n=0, t=0, T=0)
        self.assertEqual((q, delta, mu), (0.0, 0.0, 0.0))

    def test_full_capture_guarded(self):
        # m == n (mn=1) himoya sharti — xato bermasdan 0 qaytadi
        q, delta, mu = compute_q_advanced(m=10, n=10, t=100, T=3650)
        self.assertEqual(q, 0.0)
