"""Exercise the actual project_bounds C++ from the Dagor patch with minimal math stubs."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

class ProjectionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'g++ is required to compile the C++ projection test')
    def test_cpp_projection(self):
        patch = (ROOT / 'engine/patches/dagor-phase0.patch').read_text()
        start = patch.index('+bool project_bounds(')
        end = patch.index('\n+} // namespace', start)
        code = '\n'.join(line[1:] for line in patch[start:end].splitlines() if line.startswith('+'))
        prelude = r'''
#include <cmath>
#include <initializer_list>
using std::fminf; using std::fmaxf;
struct Point3 { float x,y,z; Point3(float X=0,float Y=0,float Z=0):x(X),y(Y),z(Z){} };
Point3 operator+(Point3 a,Point3 b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
Point3 operator-(Point3 a,Point3 b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
Point3 operator*(Point3 a,float s){return {a.x*s,a.y*s,a.z*s};}
struct Point4 { float x,y,z,w; Point4(float X=0,float Y=0,float Z=0,float W=0):x(X),y(Y),z(Z),w(W){} };
struct BBox3 {Point3 lo,hi; Point3 operator[](bool i) const{return i?hi:lo;}};
struct TMatrix {Point3 t;};
TMatrix inverse(TMatrix a){return {{-a.t.x,-a.t.y,-a.t.z}};}
Point3 operator*(TMatrix a,Point3 b){return a.t+b;}
Point3 operator*(Point3 b,TMatrix a){return a.t+b;}
'''
        checks = r'''
#include <cassert>
int main() {
  TMatrix id{{0,0,0}}; Point4 b;
  auto check = [&](BBox3 box, TMatrix model, bool expected) {
    return project_bounds(box,model,id,1,100,1,1,100,100,b)==expected;
  };
  assert(check({{-1,-1,4},{1,1,6}},id,true));
  assert(std::fabs(b.x-37.5f)<0.001f && std::fabs(b.z-25)<0.001f);
  assert(check({{-1,-1,-6},{1,1,-4}},id,false));
  assert(check({{-1,-1,0.5},{1,1,2}},id,true));
  assert(b.x==0 && b.y==0 && b.z==100 && b.w==100);
  assert(check({{20,-1,4},{21,1,6}},id,false));
  assert(check({{-1,-1,4},{1,1,6}},{{4,0,0}},true));
  assert(b.x>=0 && b.x+b.z<=100.001f);
  assert(check({{-1,-1,101},{1,1,102}},id,false));
}
'''
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / 'projection.cpp'
            exe = pathlib.Path(d) / 'projection'
            src.write_text(prelude + code + checks)
            subprocess.run(['g++', '-std=c++17', str(src), '-o', str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)
