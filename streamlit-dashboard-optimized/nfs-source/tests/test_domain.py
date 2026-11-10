"""Domain regressions verify column identity and source distance indexing."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from dashboard import domain


class Builder:
    def configure_column(self, *args, **kwargs):
        return self

    def configure_selection(self, *args, **kwargs):
        return self

    def build(self):
        return {}


class Context:
    iter_first = True
    only_latest = True
    star_only_latest = True
    selected_pin_name = 'DEFAULT'
    keywords = ()

    def __init__(self, payload):
        self.payload = payload
        self.rows = []
        self.issues = []

    def read_results(self, *args, **kwargs):
        return self.payload, {}

    def prepare_table(self, lines, header, head_width=350):
        self.rows = [dict(zip(header, line[3:-1])) for line in lines]
        return self.rows, [], Builder()

    def grid(self, *args, **kwargs):
        return {'selected_rows': []}

    def issue(self, message):
        self.issues.append(message)


class DomainTests(unittest.TestCase):
    def render(self, task, payload, selections=None):
        context = Context(payload)
        selections = selections or {}
        def selectbox(label, options, **kwargs):
            return selections.get(label, options[kwargs.get('index', 0)])
        with patch.multiple(
            domain.streamlit,
            columns=lambda spec: [nullcontext() for _ in range(spec if isinstance(spec, int) else len(spec))],
            selectbox=selectbox,
            radio=selectbox,
            info=lambda *a, **k: None,
            write=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            subheader=lambda *a, **k: None,
        ):
            domain.render_task(task, context)
        return context

    def test_bev_distance_values_follow_source_threshold_order(self):
        payload = {'CAR': {'IoU@0.5 PedDist@1.0': {
            'dist_thres': [30, 90, 60], 'ap': {'I_BEV': [0.3, 0.9, 0.6]}}}}
        context = self.render('bev', {('exp', 1000): payload}, {'评测指标': 'ap'})
        self.assertEqual(context.rows[0]['car(90m)'], 0.9)
        self.assertEqual(context.rows[0]['car(60m)'], 0.6)
        self.assertEqual(context.rows[0]['car(30m)'], 0.3)

    def test_bev_missing_value_occupies_its_column(self):
        payload = {'CAR': {'IoU@0.5 PedDist@1.0': {
            'dist_thres': [90, 60, 30], 'ap': {'I_BEV': [0.9]}}}}
        context = self.render('bev', {('exp', 1000): payload}, {'评测指标': 'ap'})
        self.assertEqual(context.rows[0]['car(90m)'], 0.9)
        self.assertEqual(context.rows[0]['car(60m)'], '—')
        self.assertEqual(context.rows[0]['car(30m)'], '—')
        self.assertTrue(context.issues)

    def test_new_bev_numeric_thresholds_match_string_controls(self):
        payload = {'CAR': {'IoU@0.5 PedDist@1.0': {
            'dist_thres': [150, 90, 60, 30],
            'thr_pre': {'I_BEV': [[0.15], [0.9], [0.6], [0.3]]}}}}
        context = self.render('bev-new', {('exp', 1000): payload}, {'评测指标': 'ap'})
        self.assertEqual(context.rows[0]['car(30m)'], '30.0')
        self.assertEqual(context.rows[0]['car(150m)'], '15.0')

    def test_cog_dictionary_order_does_not_move_metric_to_other_column(self):
        def metric(value):
            return {'F1': value, 'precision': value, 'Recall': value, 'IoU': value}
        context = self.render('cog', {
            ('one', 1000): {'lane': {'alpha': metric(0.1), 'beta': metric(0.2)}},
            ('two', 1000): {'lane': {'beta': metric(0.8), 'alpha': metric(0.9), 'gamma': metric(0.7)}}})
        second = context.rows[1]
        self.assertTrue(second['alpha@f1(p,r,iou)'].startswith('0.900('))
        self.assertTrue(second['beta@f1(p,r,iou)'].startswith('0.800('))
        self.assertEqual(context.rows[0]['gamma@f1(p,r,iou)'], '—')

    def test_pv_class_names_determine_column_order(self):
        payload = {'cls': ['vehicle', 'ground'], 'mIoU': 0.5, 'mPrecision': 0.5,
                   'mRecall': 0.5, 'iou': [0.8, 0.2], 'precision': [0.7, 0.3],
                   'recall': [0.6, 0.4]}
        context = self.render('pvseg', {('exp', 1000): payload})
        self.assertEqual(context.rows[0]['ground(IoU/Pr/Rc)'], '0.2/0.3/0.4')
        self.assertEqual(context.rows[0]['vehicle(IoU/Pr/Rc)'], '0.8/0.7/0.6')
        self.assertEqual(context.rows[0]['sidewalk(IoU/Pr/Rc)'], '—')

    def test_god_obstacle_missing_metric_stays_missing_and_real_zero_is_preserved(self):
        payload = {'distance': {'God obstacle detection distance @center dist [1, 5]': {
            'ALL': {'stable_det_dist': 0, 'max_det_dist': 5.5},
            'Cone': {'max_det_dist': 2.4},
        }}}
        context = self.render('god', {('obstacle_exp', 12000): payload},
                              {'评测指标': 'God Obstacle'})
        self.assertEqual(len(context.rows), 1)
        row = context.rows[0]
        self.assertEqual(row['ALL-Stable'], '0.00')
        self.assertEqual(row['ALL-Max'], '5.50')
        self.assertEqual(row['Cone-Stable'], 'Na')
        self.assertEqual(row['Cone-Max'], '2.40')
        self.assertEqual(row['Fake_Pedestrian_Car-Stable'], 'Na')
        self.assertTrue(any('obstacle_exp@12000' in message
                            and 'Cone/stable_det_dist' in message
                            for message in context.issues))
        self.assertFalse(any('not defined' in message for message in context.issues))

    def test_broken_cog_record_does_not_hide_good_record(self):
        context = self.render('cog', {
            ('bad', 1000): {},
            ('good', 1000): {'lane': {'alpha': {'F1': 1, 'precision': 1, 'Recall': 1, 'IoU': 1}}}})
        self.assertEqual(len(context.rows), 1)
        self.assertIn('good', context.rows[0]['Exp'])
        self.assertTrue(context.issues)


if __name__ == '__main__':
    unittest.main()
