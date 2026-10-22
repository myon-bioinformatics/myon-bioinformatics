import unittest
from tools import constrained_lab_algorithms as algorithms

class AlgorithmTests(unittest.TestCase):
    def test_bitset_matches_reference(self):
        for seed in range(10):
            result = algorithms.benchmark(seed, width=73, trials=2)
            self.assertTrue(result["equivalent"])
    def test_empty_and_duplicate_sets(self):
        self.assertEqual(algorithms.bitset_intersection([], [], 0), [])
        self.assertEqual(algorithms.bitset_intersection([1,1,3], [3,3], 4), [3])
    def test_invalid_index(self):
        with self.assertRaises(ValueError):
            algorithms.bitset_intersection([4], [0], 4)
    def test_tile_deduplication(self):
        image = [[1,0,1,0],[0,1,0,1],[1,0,1,0],[0,1,0,1]]
        count, indices = algorithms.tile_deduplicate(image, 2)
        self.assertEqual(count, 1)
        self.assertEqual(indices, [0,0,0,0])
    def test_ragged_and_partial_tiles(self):
        for image in ([[1],[1,2]], [[1,2,3],[1,2,3]]):
            with self.assertRaises(ValueError):
                algorithms.tile_deduplicate(image, 2)
