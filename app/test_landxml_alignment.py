"""Regression coverage for LandXML through the existing alignment reader."""
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from core import Alignment


def circle():
    cardinal = ('0 4', '4 0', '0 -4', '-4 0', '0 4')
    return ''.join(f'<Curve radius="4" rot="ccw"><Start>{a}</Start><End>{b}</End><Center>0 0</Center></Curve>'
                   for a, b in zip(cardinal, cardinal[1:]))


def document(plan, profile, length, start=0):
    return f'''<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.1">
    <Units xmlns="http://www.landxml.org/schema/LandXML-1.0"><Metric linearUnit="meter"/></Units>
    <Alignments><Alignment staStart="{start}" length="{length}">
      <CoordGeom>{plan}</CoordGeom><Profile><ProfAlign>{profile}</ProfAlign></Profile>
      <TunnelProfiles><TunnelProfile><TunnelProfSurf name="1. Intrados">
        <TunnelCoordGeom>{circle()}</TunnelCoordGeom>
      </TunnelProfSurf></TunnelProfile></TunnelProfiles>
    </Alignment></Alignments></LandXML>'''


class LandXMLAlignmentTest(unittest.TestCase):
    def read(self, contents, extension='.xml', radius=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ('tunnel' + extension)
            path.write_text(contents, encoding='utf-8')
            return Alignment.read(path, radius)

    def test_plan_arc_and_vertical_coverage(self):
        plan = '<Curve radius="20" rot="ccw"><Start>200 120</Start><End>220 100</End><Center>200 100</Center></Curve>'
        axis = self.read(document(plan, '<PVI>0 30</PVI><PVI>50 35</PVI>', 10*math.pi, -1), '.landxml')
        self.assertEqual(axis.a[0, 0], 0)
        self.assertAlmostEqual(axis.a[-1, 0], 10*math.pi-1)
        station = 10*math.pi/2-1
        centre, _, _, _, radius = axis.at(station)
        np.testing.assert_allclose(centre, [100+math.sqrt(200), 200+math.sqrt(200), 30+station*.1], atol=.00025, rtol=0)
        self.assertEqual(radius, 4)

    def test_circular_vertical_curve_is_not_its_pvi(self):
        plan = '<Line><Start>200 100</Start><End>200 200</End></Line>'
        radius = 100
        tangent = radius * math.tan(math.atan(.2)/2)
        length = tangent * (1 + math.cos(math.atan(.2)))
        profile = f'<PVI>0 20</PVI><CircCurve radius="{radius}" length="{length}">50 20</CircCurve><PVI>100 30</PVI>'
        axis = self.read(document(plan, profile, 100))
        height = 20 + radius - math.sqrt(radius**2 - tangent**2)
        self.assertAlmostEqual(axis.at(50)[0][2], height, places=9)
        self.assertAlmostEqual(axis.at(100)[0][2], 30)

    def test_rejects_non_landxml_and_document_type(self):
        with self.assertRaisesRegex(ValueError, 'Choose a LandXML'):
            self.read('<settings/>')
        with self.assertRaisesRegex(ValueError, 'document type'):
            self.read('<!DOCTYPE LandXML [<!ENTITY t "unsupported">]><LandXML>&t;</LandXML>')

    def test_radius_needed_only_when_profile_is_missing(self):
        plan = '<Line><Start>200 100</Start><End>200 200</End></Line>'
        xml = document(plan, '<PVI>0 20</PVI><PVI>100 20</PVI>', 100)
        self.assertEqual(self.read(xml, radius=3).at(50)[-1], 4)
        missing = xml[:xml.index('<TunnelProfiles>')] + xml[xml.index('</TunnelProfiles>')+len('</TunnelProfiles>'):]
        with self.assertRaisesRegex(ValueError, 'Enter the design radius'):
            self.read(missing)
        self.assertEqual(self.read(missing, radius=3).at(50)[-1], 3)

    def test_csv_still_loads(self):
        axis = self.read('chainage,e,n,h,radius\n0,100,200,30,4\n2,102,200,30,4\n', '.csv')
        self.assertEqual(axis.at(1)[0].tolist(), [101, 200, 30])
