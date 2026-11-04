import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {laserLineSegments,xAtYOnLaserLine} from '../js/geometry/lines.js';
import {resolveLaserStitchPose,migrateLaserLinesDoc} from '../js/laser-stitch.js';
globalThis.window={location:{search:''}};
const {collectAlignmentSegments,snapLabPointUnified,refreshAlignmentIntersectionCache}=await import('../js/canvas/alignment-snap.js');
import {store} from '../js/state/store.js';

const read=(relative)=>JSON.parse(readFileSync(new URL(relative,import.meta.url),'utf8'));
const doc=read('../../schemas/coordinator_seeds/real.default/laser_lines.json');
const line=doc.lines.find(row=>row.id==='he_ne');
const base=read('../../../lab_automation/data/calibration/radial_motion_hene_tag22_calibration.json');
const placed=read('../../../lab_automation/data/calibration/radial_motion_hene_tag22_placed_calibration.json');

test('render geometry preserves six finite segments and fractional station targets',()=>{
    const segments=laserLineSegments(line);
    assert.equal(segments.length,6);
    assert.deepEqual(segments[0][0],line.points[0]);
    assert.deepEqual(segments.at(-1)[1],line.points.at(-1));
    const theta=base.frame_transform.lab_to_robot_rotation_rad;
    const [ox,oy]=base.frame_transform.robot_origin_xy_mm;
    for(let i=0;i<=60;i++){
        const t=i/10, ry=-300.+75.*t;
        const rows=placed.correction_model.points, k=Math.min(Math.floor(t),5);
        const dx=rows[k].robot_tcp_x_correction_mm+(t-k)*
            (rows[k+1].robot_tcp_x_correction_mm-rows[k].robot_tcp_x_correction_mm);
        const rx=base.robot_tcp_line.a*ry+base.robot_tcp_line.b+dx;
        const lx=Math.cos(theta)*(rx-ox)+Math.sin(theta)*(ry-oy);
        const ly=-Math.sin(theta)*(rx-ox)+Math.cos(theta)*(ry-oy);
        assert.ok(Math.abs(xAtYOnLaserLine(line,ly)-lx)<1e-9);
    }
    assert.equal(xAtYOnLaserLine(line,line.points[0].y-1),null);
    assert.equal(xAtYOnLaserLine(line,line.points.at(-1).y+1),null);
});

test('orange calibrated He-Ne accepts components and keeps finite range',()=>{
    store.laserLinesDoc=doc;
    const y=line.points[2].y;
    const pose=resolveLaserStitchPose(y,-90,line.id,'tag_22');
    assert.equal(pose.ok,true);
    assert.ok(Math.abs(pose.x-line.points[2].x)<1e-9);
    assert.equal(resolveLaserStitchPose(y,-90,line.id,'tag_14').ok,true);
    assert.equal(resolveLaserStitchPose(y,90,line.id,'tag_22').ok,true);
    assert.equal(resolveLaserStitchPose(-400,-90,line.id,'tag_22').ok,false);
    assert.equal(resolveLaserStitchPose(y,270,line.id,'tag_22').ok,true);
});

test('two visible lasers: unchanged red diode and calibrated orange default',()=>{
    store.laserLinesDoc=doc; store.guideLines=[];
    assert.deepEqual(doc.lines.map(row=>row.id),['diode','he_ne']);
    assert.equal(line.color,'#ff5c33');
    assert.equal(resolveLaserStitchPose(0,-90).lineId,'he_ne');
    assert.equal(resolveLaserStitchPose(0,0,'diode').x,392.6);
    assert.equal(resolveLaserStitchPose(400,-90,'he_ne','tag_14').ok,false);
    assert.equal(collectAlignmentSegments().length,7);
});

test('drag snaps to every fractional station, without gluing to calibration knots',()=>{
    store.laserLinesDoc=doc; store.guideLines=[];
    assert.equal(refreshAlignmentIntersectionCache().length,0);
    let prevGhost=null,stickySegIdx=null;
    for(let station=0;station<=600;station++){
        const t=station/100, k=Math.min(Math.floor(t),5), f=t-k;
        const a=line.points[k],b=line.points[k+1];
        const x=a.x+f*(b.x-a.x), y=a.y+f*(b.y-a.y);
        const len=Math.hypot(b.x-a.x,b.y-a.y);
        // Offset normal to the path so the exact nearest point is known.
        const offset=Number.isInteger(t)?0:3;
        const snapped=snapLabPointUnified(x+offset*(b.y-a.y)/len,y-offset*(b.x-a.x)/len,
            {prevGhost,stickySegIdx});
        assert.equal(snapped.snapKind,'segment');
        assert.ok(Math.hypot(snapped.x-x,snapped.y-y)<1e-8,JSON.stringify({t,snapped,x,y}));
        prevGhost={x:snapped.x,y:snapped.y};
        stickySegIdx=snapped.segIdx;
    }
});

test('no snap to an extrapolated orange line; visibility toggle is respected',()=>{
    store.laserLinesDoc=doc;store.guideLines=[];
    const end=line.points.at(-1);
    assert.equal(snapLabPointUnified(end.x,end.y+50).snapKind,'none');
    store.laserLinesDoc={...doc,lines:doc.lines.map(row=>row.id==='he_ne'?{...row,enabled:false}:row)};
    assert.equal(collectAlignmentSegments().length,1);
    const point=line.points[2];
    assert.equal(snapLabPointUnified(point.x,point.y).snapKind,'none');
});

test('guide crossings respect finite calibration segments',()=>{
    store.laserLinesDoc={...doc,lines:[line]};
    const a=line.points[2],b=line.points[3],x=(a.x+b.x)/2,y=(a.y+b.y)/2;
    store.guideLines=[{id:'crossing',p1:{x:0,y},p2:{x:490,y}}];
    assert.equal(refreshAlignmentIntersectionCache().length,1);
    const snap=snapLabPointUnified(x+2,y+2);
    assert.equal(snap.snapKind,'intersection');
    assert.ok(Math.hypot(snap.x-x,snap.y-y)<1e-8);
    const outsideY=line.points.at(-1).y+60;
    store.guideLines=[{id:'outside',p1:{x:0,y:outsideY},p2:{x:490,y:outsideY}}];
    assert.equal(refreshAlignmentIntersectionCache().length,0);
    store.guideLines=[];
});

test('old three-line sessions become one orange calibrated He-Ne plus diode',()=>{
    const calibration={...line.calibration};delete calibration.usage;
    const old={snap_line_id:'diode',lines:[
        doc.lines[0],{id:'he_ne',p1:{x:360,y:-193.3},p2:{x:360.4,y:206.7}},
        {...line,id:'he_ne_radial_tag22',color:'#22d3ee',calibration},
    ]};
    const migrated=migrateLaserLinesDoc(old);
    assert.equal(migrated.snap_line_id,'he_ne');
    assert.equal(migrated.lines.length,2);
    assert.deepEqual(migrated.lines.find(row=>row.id==='he_ne').points,line.points);
    assert.equal(migrated.lines.find(row=>row.id==='he_ne').color,'#ff5c33');
    assert.deepEqual(migrateLaserLinesDoc(migrated),migrated);
    store.laserLinesDoc=old;
    assert.equal(resolveLaserStitchPose(line.points[2].y,-90,'he_ne_radial_tag22','tag_22').lineId,'he_ne');
});
