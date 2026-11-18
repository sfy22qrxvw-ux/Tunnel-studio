const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/web/job-ui.js','utf8');
const callback=source.match(/ jobScene\.onNavigate=\(\)=>\{.*?\};/)[0];
for(const [scale,covered,setup,expected] of [[5,true,false,1],[9,true,false,0],[9,false,false,1],[5,true,true,0]]){
 let aborts=0,scheduled=0;
 const c={jobScene:{scale},detailView:{scale:10},setupState:setup,previewEpoch:7,previewRequest:{controller:{abort:()=>aborts++}},viewStillCovered:()=>covered,scheduleDetail:()=>scheduled++};
 vm.runInNewContext(callback,c);c.jobScene.onNavigate();assert.equal(aborts,expected);assert.equal(c.previewEpoch,7+expected);assert.equal(scheduled,1);
}
console.log('Zoom and pan cancel obsolete requests; covered movement and setup navigation preserved.');
