import math
import threading
import unittest

from vimcommander import step


def box_step(size=(2.0, 3.0, 1.0), assembly=None, color=None) -> str:
    """A STEP file with one box solid (six plane faces). With `assembly` = (dx, dy, dz),
    the box is a component placed at that offset in an assembly."""
    lines = []
    n = [0]

    def add(text: str) -> int:
        n[0] += 1
        lines.append(f"#{n[0]} = {text};")
        return n[0]

    sx, sy, sz = size
    corners = [(x, y, z) for z in (0.0, sz) for y in (0.0, sy) for x in (0.0, sx)]
    verts = [add(f"VERTEX_POINT('',#{add(f'CARTESIAN_POINT({chr(39)}{chr(39)},({x},{y},{z}))')})")
             for x, y, z in corners]
    edges = {}

    def edge(a, b):
        key = (min(a, b), max(a, b))
        if key not in edges:
            pa = corners[key[0]]
            d = add(f"DIRECTION('',({corners[key[1]][0] - pa[0]},{corners[key[1]][1] - pa[1]},"
                    f"{corners[key[1]][2] - pa[2]}))")
            line = add(f"LINE('',#{add(f'CARTESIAN_POINT({chr(39)}{chr(39)},{pa})')},#{add(f'VECTOR({chr(39)}{chr(39)},#{d},1.)')})")
            edges[key] = add(f"EDGE_CURVE('',#{verts[key[0]]},#{verts[key[1]]},#{line},.T.)")
        return edges[key], a < b

    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    normals = [(0, 0, -1), (0, 0, 1), (0, -1, 0), (0, 1, 0), (-1, 0, 0), (1, 0, 0)]
    faces = []
    for quad, normal in zip(quads, normals):
        oes = []
        for i in range(4):
            e, forward = edge(quad[i], quad[(i + 1) % 4])
            oes.append(add(f"ORIENTED_EDGE('',*,*,#{e},{'.T.' if forward else '.F.'})"))
        loop = add("EDGE_LOOP('',(" + ",".join(f"#{o}" for o in oes) + "))")
        bound = add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
        origin = add(f"CARTESIAN_POINT('',{corners[quad[0]]})")
        axis = add(f"AXIS2_PLACEMENT_3D('',#{origin},#{add(f'DIRECTION({chr(39)}{chr(39)},{normal})')},$)")
        faces.append(add(f"ADVANCED_FACE('a face; with a '' quote',(#{bound}),#{add(f'PLANE({chr(39)}{chr(39)},#{axis})')},.T.)"))
    shell = add("CLOSED_SHELL('',(" + ",".join(f"#{f}" for f in faces) + "))")
    solid = add(f"MANIFOLD_SOLID_BREP('box',#{shell})")
    if color:
        rgb = add(f"COLOUR_RGB('',{color[0]},{color[1]},{color[2]})")
        fill = add(f"FILL_AREA_STYLE('',(#{add(f'FILL_AREA_STYLE_COLOUR({chr(39)}{chr(39)},#{rgb})')}))")
        side = add(f"SURFACE_SIDE_STYLE('',(#{add(f'SURFACE_STYLE_FILL_AREA(#{fill})')}))")
        usage = add(f"SURFACE_STYLE_USAGE(.BOTH.,#{side})")
        add(f"STYLED_ITEM('color',(#{add(f'PRESENTATION_STYLE_ASSIGNMENT((#{usage}))')}),#{solid})")
    origin = add("AXIS2_PLACEMENT_3D('',#%d,$,$)" % add("CARTESIAN_POINT('',(0.,0.,0.))"))
    part = add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{solid},#{origin}),#999)")
    if assembly:
        top_origin = add("AXIS2_PLACEMENT_3D('',#%d,$,$)" % add("CARTESIAN_POINT('',(0.,0.,0.))"))
        z = add("DIRECTION('',(0.,0.,1.))")
        x = add("DIRECTION('',(0.,1.,0.))")  # turned by 90 degrees about z
        placed = add(f"AXIS2_PLACEMENT_3D('',#{add(f'CARTESIAN_POINT({chr(39)}{chr(39)},{assembly})')},#{z},#{x})")
        top = add(f"SHAPE_REPRESENTATION('',(#{top_origin},#{placed}),#999)")
        transform = add(f"ITEM_DEFINED_TRANSFORMATION('','',#{top_origin},#{placed})")
        add(f"( REPRESENTATION_RELATIONSHIP('','',#{part},#{top}) "
            f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{transform}) SHAPE_REPRESENTATION_RELATIONSHIP() )")
        for name, rep in (("BOARD", top), ("R1206", part)):  # product names, as Altium writes them
            product = add(f"PRODUCT('{name}','{name}','',(#998))")
            formation = add(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
            definition = add(f"PRODUCT_DEFINITION('design','',#{formation},#997)")
            add(f"SHAPE_DEFINITION_REPRESENTATION(#{add(f'PRODUCT_DEFINITION_SHAPE({chr(39)}{chr(39)},{chr(39)}{chr(39)},#{definition})')},#{rep})")
    return ("ISO-10303-21;\nHEADER;\nFILE_NAME('box.step','2026-01-01',(''),(''),'test','Tester 1','');\n"
            "ENDSEC;\nDATA;\n" + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")


class ParseTest(unittest.TestCase):
    def test_entities(self):
        f = step.StepFile("ISO-10303-21;\nDATA;\n"
                          "#1 = PRODUCT('a;b','it''s',$,(#2,#3));\n"
                          "#2 = ( A(1.5,.T.) B((1,2),LENGTH_MEASURE(2.E-3),*) );\n"
                          "#3 = DIRECTION('',(0.,-1.,0.));\nENDSEC;")
        self.assertEqual(f.args(1), ["a;b", "it's", None, [2, 3]])
        self.assertIsInstance(f.args(1)[3][0], step.Ref)
        self.assertEqual(f.get(2), {"A": [1.5, True], "B": [[1.0, 2.0], 0.002, None]})
        self.assertEqual(f.kinds(2), ["A", "B"])
        self.assertEqual(f.kind(3), "DIRECTION")
        self.assertEqual(f.by_type("B"), [2])

    def test_not_step(self):
        with self.assertRaises(ValueError):
            step.parse(b"probe begin { println(1) }")  # a SystemTap .stp script
        self.assertFalse(step.looks_like_step(b"probe begin"))
        self.assertTrue(step.looks_like_step(b"ISO-10303-21;\nHEADER;"))
        self.assertTrue(step.is_step("x/Board.STEP") and step.is_step("a.stp"))

    def test_stop(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(step.Stopped):
            step.parse(box_step(), stop)


class ModelTest(unittest.TestCase):
    def test_box(self):
        m = step.parse(box_step())
        self.assertEqual((m.solids, m.faces), (1, 6))
        planes = [p for p in m.pieces if p.edges]
        self.assertEqual(len(planes), 6)
        self.assertTrue(all(len(p.loops[0]) == 4 for p in planes))
        self.assertEqual(m.bounds, ((0.0, 0.0, 0.0), (2.0, 3.0, 1.0)))
        self.assertEqual(m.source, "Tester 1")

    def test_color(self):
        m = step.parse(box_step(color=(0.1, 0.5, 0.9)))
        self.assertTrue(all(p.color == (0.1, 0.5, 0.9) for p in m.pieces))
        self.assertTrue(all(p.color == step.DEFAULT_COLOR for p in step.parse(box_step()).pieces))

    def test_assembly_placement(self):
        m = step.parse(box_step(assembly=(10.0, 20.0, 5.0)))
        lo, hi = m.bounds
        # turned 90 degrees about z (x axis along +y), then moved
        for got, want in zip(lo + hi, (7.0, 20.0, 5.0, 10.0, 22.0, 6.0)):
            self.assertAlmostEqual(got, want)
        # the part's name; the top product (the board file itself) is left out
        self.assertEqual({m.labels[i] for i in m.part_of}, {"R1206"})
        self.assertEqual(len(m.part_of), len(m.pieces))

    def test_circle_and_cylinder(self):
        text = """ISO-10303-21;
DATA;
#1 = CARTESIAN_POINT('',(0.,0.,0.));
#2 = DIRECTION('',(0.,0.,1.));
#3 = DIRECTION('',(1.,0.,0.));
#4 = AXIS2_PLACEMENT_3D('',#1,#2,#3);
#5 = CIRCLE('',#4,2.);
#6 = CARTESIAN_POINT('',(2.,0.,0.));
#7 = VERTEX_POINT('',#6);
#8 = EDGE_CURVE('',#7,#7,#5,.T.);
#9 = CARTESIAN_POINT('',(0.,0.,3.));
#10 = AXIS2_PLACEMENT_3D('',#9,#2,#3);
#11 = CIRCLE('',#10,2.);
#12 = CARTESIAN_POINT('',(2.,0.,3.));
#13 = VERTEX_POINT('',#12);
#14 = EDGE_CURVE('',#13,#13,#11,.T.);
#15 = EDGE_LOOP('',(#16));
#16 = ORIENTED_EDGE('',*,*,#8,.T.);
#17 = EDGE_LOOP('',(#18));
#18 = ORIENTED_EDGE('',*,*,#14,.F.);
#19 = CYLINDRICAL_SURFACE('',#4,2.);
#20 = ADVANCED_FACE('',(#21,#22),#19,.T.);
#21 = FACE_OUTER_BOUND('',#15,.T.);
#22 = FACE_BOUND('',#17,.T.);
#23 = OPEN_SHELL('',(#20));
#24 = SHELL_BASED_SURFACE_MODEL('',(#23));
ENDSEC;
"""
        m = step.parse(text)
        cells = [p for p in m.pieces if p.fill]
        self.assertEqual(len(cells), round(2 * math.pi / step.ANGLE_STEP))  # one band around the axis
        for p in cells:
            for q in p.loops[0]:
                self.assertAlmostEqual(math.hypot(q[0], q[1]), 2.0)
            self.assertAlmostEqual(p.normal[2], 0.0)
        outline = [p for p in m.pieces if not p.fill]
        self.assertEqual(len(outline), 1)
        self.assertEqual(len(outline[0].loops), 2)
        self.assertEqual(sorted(outline[0].marks), [(0.0, 0.0, 0.0), (0.0, 0.0, 3.0)])  # circle centres, to snap to
        lo, hi = m.bounds
        self.assertAlmostEqual(lo[2], 0.0)
        self.assertAlmostEqual(hi[2], 3.0)
        self.assertAlmostEqual(hi[0], 2.0)

    def test_bspline_curve(self):
        # a quadratic B-spline from (0,0,0) to (2,0,0) bulging up to y = 0.5
        f = step.StepFile("ISO-10303-21;\nDATA;\n"
                          "#1 = CARTESIAN_POINT('',(0.,0.,0.));\n#2 = CARTESIAN_POINT('',(1.,1.,0.));\n"
                          "#3 = CARTESIAN_POINT('',(2.,0.,0.));\n"
                          "#4 = B_SPLINE_CURVE_WITH_KNOTS('',2,(#1,#2,#3),.UNSPECIFIED.,.F.,.F.,(3,3),(0.,1.),"
                          ".UNSPECIFIED.);\nENDSEC;")
        b = step._Builder(f)
        pts = b.curve_points(4, (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), True)
        self.assertEqual(pts[0], (0.0, 0.0, 0.0))
        self.assertEqual(pts[-1], (2.0, 0.0, 0.0))
        self.assertAlmostEqual(max(p[1] for p in pts), 0.5)


def cube_stl_ascii() -> bytes:
    """A unit cube, two triangles per side."""
    c = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    out = ["solid cube"]
    for a, b, cc, d in quads:
        for tri in ((a, b, cc), (a, cc, d)):
            out.append("  facet normal 0 0 0\n    outer loop")
            out += [f"      vertex {c[i][0]} {c[i][1]} {c[i][2]}" for i in tri]
            out.append("    endloop\n  endfacet")
    out.append("endsolid cube")
    return "\n".join(out).encode()


class StlTest(unittest.TestCase):
    def test_ascii_cube(self):
        from vimcommander import stl
        m = stl.parse(cube_stl_ascii())
        triangles = [p for p in m.pieces if p.fill]
        self.assertEqual(len(triangles), 12)
        self.assertTrue(m.mesh)
        self.assertEqual(m.bounds, ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
        edges = [p for p in m.pieces if not p.fill]
        self.assertEqual(len(edges), 12)  # the cube's edges; the diagonals are flat

    def test_binary(self):
        import struct
        from vimcommander import stl
        tris = [((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 0, 0), (0, 1, 0), (0, 0, 1))]
        data = b"solid but binary".ljust(80, b" ") + struct.pack("<I", len(tris))
        for t in tris:
            data += struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0)
        m = stl.parse(data)
        self.assertEqual(m.faces, 2)
        self.assertEqual(len([p for p in m.pieces if not p.fill]), 5)  # 4 border edges + the fold
        with self.assertRaises(ValueError):
            stl.parse(bytes(range(256)) * 4)  # a Windows certificate trust list, say


if __name__ == "__main__":
    unittest.main()
