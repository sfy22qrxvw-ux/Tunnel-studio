/* Copyright (c) 2026 Damian Gulczynski. Practical, keyboard-accessible help. */
'use strict';
(()=>{
 const explanations={
  'width':'Thickness of the section slice, in metres. A thicker slice contains more points but mixes geometry from a longer part of the tunnel.',
  'band':'Distance either side of the design radius included in tunnel analysis, in metres. Increase it to include larger departures; decrease it to exclude interior equipment.',
  'tolerance':'Allowed radial difference from the design, in millimetres. It changes the pass/fail assessment, not the measured points.',
  'creep-limit':'Allowed difference between measured and design ring-face chainage, in millimetres. Use your project acceptance limit.',
  'mesh-name':'Name shown for the new mesh in the left object explorer.',
  'mesh-engine':'How triangles are constructed from the selected measured surface. Read the explanation below after choosing a method.',
  'mesh-use-selection':'Use retained points inside your current box, polygon or lasso. With no selection, the retained points of the selected scan are used. A visible limit box also restricts the source area.',
  'mesh-quality':'Sets surface detail and automatic working limits. Create Tunnel Surface reads all retained measurements at every quality setting. Fine sets a finer default grid and can take longer. Display point density is separate.',
  'mesh-sampling-distance':'Spatial sampling cell size for generic methods, in metres. For example, 0.01 means 10 mm. Larger cells keep fewer measured representatives. Zero manages spacing automatically. Disabled for Create Tunnel Surface, which uses all retained support.',
  'mesh-noise-gap':'Neighbour gap used by the optional generic mesh-input filter, in metres. Zero disables it. Too small can discard valid sparse surface points. For Create Tunnel Surface, use the separate Cleaning tools before meshing.',
  'mesh-step':'Surface grid spacing, in millimetres, for grid-based methods. Smaller spacing can follow finer detail but increases cells and triangles. This is not survey accuracy.',
  'mesh-edge':'Longest permitted triangle edge, in metres, where supported by the selected method. Smaller values leave more gaps; larger values can bridge openings. Zero uses the method default.',
  'mesh-smoothing':'Number of smoothing passes applied to the created mesh. Zero preserves the unsmoothed result. More passes can reduce roughness and also remove genuine detail.',
  'mesh-tunnel-gap':'Largest small gap to interpolate in the tunnel grid, in metres. Zero preserves gaps. Interpolated surface is inferred; it is not a new measured observation.',
  'mesh-alpha-radius':'Radius limit for 3D Alpha Shape, in metres. A larger radius connects broader regions; a smaller radius preserves more openings. Zero chooses an automatic estimate.',
  'mesh-poisson-depth':'Detail level for Screened Poisson reconstruction. Each extra level can substantially increase processing and memory. Start at 8 and inspect the surface before increasing it.',
  'mesh-hole-handling':'Choose whether to preserve open boundaries or attempt small boundary repairs. Review inferred triangles before using the result for inspection or volume.',
  'mesh-hole-size':'Largest boundary opening eligible for repair, in metres. This refers to its maximum extent, including the length of a narrow opening.',
  'mesh-degrees':'Angular spacing around the tunnel, in degrees. Smaller values create more circumferential cells and require denser measured support.',
  'mesh-ball-radius':'Ball Pivoting search radius, in metres. It needs to match point spacing. Zero estimates the radius; inspect missed areas and bridged gaps.',
  'mesh-refine-name':'Name of the derived mesh. The source mesh remains available.',
  'mesh-refine-smoothing':'Number of Taubin smoothing passes. More passes suppress more local roughness and can alter small fittings or sharp features.',
  'mesh-refine-triangles':'Desired number of triangles after reduction. A lower count is lighter to display and export but preserves less surface detail.',
  'mesh-workflow':'Preset for the alignment-grid tunnel workflow. It changes grid spacing, interpolation and related settings; inspect them before applying.',
  'mesh-start':'Start chainage along the active alignment, in metres.',
  'mesh-end':'End chainage along the active alignment, in metres. It must be after the start.',
  'mesh-method':'How each alignment-grid cell obtains a measured radius. Averaging reduces local noise; nearest uses a measured point; IDW blends nearby support.',
  'mesh-power':'Inverse-distance weighting power. Higher values give nearby points more influence; lower values blend across a wider neighbourhood.',
  'mesh-min':'Minimum measured points required to support an alignment-grid cell. Higher values reject weak cells and can leave more gaps.',
  'mesh-tx':'Translation along Easting / X, in metres. Review against fixed survey control before applying.',
  'mesh-ty':'Translation along Northing / Y, in metres.',
  'mesh-tz':'Translation along Height / Z, in metres.',
  'mesh-rotation':'Rotation about the vertical axis, in degrees.',
  'mesh-scale':'Uniform mesh scale. One keeps the existing size. This is an explicit model transformation, not an automatic unit conversion.',
  'analysis-qa-source':'Scan whose point spacing and local roughness will be checked.',
  'analysis-qa-points':'Maximum working sample for the point-quality check. This is a diagnostic sample, not a full-density clearance assessment.',
  'analysis-k':'Nearby points used to estimate local direction and roughness. More neighbours average over a wider area; fewer follow smaller features.',
  'analysis-max-points':'Maximum sample used by this older comparison. Use Analyse → Cloud vs Cloud for the full retained-point comparison.',
  'analysis-cutoff':'Ignore distances above this value, in metres. Zero keeps all measured comparison distances.',
  'design-source':'Positioned scan to compare with the design. It must already be in the same coordinate system and metre units.',
  'design-mode':'Circular design measures radial departure from an alignment. Mesh design measures the gap to the chosen triangle surface.',
  'design-tolerance':'Allowed difference from design in millimetres. Values beyond it are highlighted as outside tolerance.',
  'design-band':'Maximum distance included in the comparison statistics. Zero disables distance exclusion. Check excluded counts before accepting the result.',
  'heat-step':'Length of each heatmap cell along chainage, in metres. Smaller cells show finer local variation but need more measured support.',
  'heat-degrees':'Width of each heatmap cell around the tunnel, in degrees. Smaller cells show finer circumferential detail.',
  'heat-mode':'Greatest absolute deviation preserves local extremes. Mean averages all retained support and can hide small defects. Grey cells have no retained data.',
  'heat-start':'First chainage included in the heatmap, in metres.',
  'heat-end':'Last chainage included in the heatmap, in metres.',
  'joint-pitch':'Expected ring width along chainage, in metres. This guides review; measured seam evidence is still required.',
  'joint-bin':'Spacing of chainage bins used to locate possible joints, in metres. Smaller bins can resolve finer positions but need denser support.',
  'joint-threshold':'Minimum radial change supporting a possible joint, in millimetres. Higher values reject shallower surface changes; lower values find more candidates.',
  'joint-band':'Band around the expected tunnel wall, in metres. It helps keep interior equipment out of the joint calculation.',
  'joint-coverage':'How much of the circumference must contain measured support. Higher coverage is more selective and may reject partial scans.',
  'joint-start':'Start of the joint-search range along the alignment, in metres.',
  'joint-end':'End of the joint-search range along the alignment, in metres.',
  'reg-limit':'Largest residual accepted for the registration review, in millimetres. Residuals are the differences left after the fit.',
  'report-step':'Distance between report sections, in metres. The report contains at most 100 sections; reduce the range or increase this spacing for longer tunnels.',
  'report-start':'First report section chainage, in metres.',
  'report-end':'End of the report section range, in metres.'
 };
 const paper={
  gusac_distance_mm:'Distance from a local fitted lining patch allowed for a point, in millimetres. Smaller values are stricter; larger values preserve rough lining and may also retain fittings.',
  gusac_sector_deg:'Angular width of local lining patches, in degrees. Smaller patches follow finer changes but need denser support.',
  gusac_window_m:'Length of local lining patches along the fitted tunnel direction, in metres. Smaller windows follow local shape changes and increase processing.',
  gusac_normal_deg:'Allowed difference between local surface directions, in degrees. A smaller value rejects more surfaces that turn away from the lining.',
  gusac_min_support:'Minimum measured support for a local plane fit. Increasing it makes fits more selective; unsupported regions remain reviewable.',
  lining_seam_depth_mm:'Minimum shallow-joint depth in millimetres, separate from bolt-pocket depth. The detector also checks measured noise and neighbouring lining. Lower values can include roughness; higher values can miss shallow seams.',
  tmatch_radius_mm:'Neighbour connection radius for lining clusters, in millimetres. Larger values can join nearby pieces; smaller values can split sparse lining into more groups.',
  tmatch_min_points:'Minimum connected measured points for a lining cluster. Smaller groups stay unresolved instead of being presented as supported panels.',
  tmatch_ring_width_m:'Expected ring width in metres. Zero uses observed gaps only. A positive width guides grouping but does not prove the actual ring boundaries.'
 };
 const tooltip=document.createElement('div');tooltip.className='ribbon-help-card';tooltip.id='parameter-help-popup';tooltip.setAttribute('role','tooltip');tooltip.hidden=true;document.body.append(tooltip);
 let timer=null,owner=null,enabled=false;
 try{enabled=localStorage.getItem('tunnelstudio.explainParameters')==='on';}catch(_){ }
 function hideRibbonHint(){clearTimeout(timer);timer=null;owner=null;tooltip.hidden=true;}
 function showHelp(el,label,text,reason='',delay=300){clearTimeout(timer);owner=el;timer=setTimeout(()=>{if(owner!==el||!el.isConnected||!el.getClientRects().length)return;tooltip.replaceChildren();const b=document.createElement('b');b.textContent=label;tooltip.append(b,document.createTextNode(text));if(reason){const p=document.createElement('span');p.className='unavailable';p.textContent=reason;tooltip.append(p);}tooltip.hidden=false;const r=el.getBoundingClientRect(),t=tooltip.getBoundingClientRect();tooltip.style.left=Math.max(10,Math.min(innerWidth-t.width-10,r.left))+'px';tooltip.style.top=(r.bottom+8+t.height<innerHeight?r.bottom+8:Math.max(10,r.top-t.height-8))+'px';},delay);}
 function ribbonHint(el,command,reason){showHelp(el,command.label,command.description,reason);}
 function apply(){document.body.classList.toggle('explain-parameters',enabled);for(const input of document.querySelectorAll('[data-parameter-help-toggle]'))input.checked=enabled;try{localStorage.setItem('tunnelstudio.explainParameters',enabled?'on':'off');}catch(_){} }
 function toggle(){enabled=!enabled;apply();return enabled;}
 function attach(el,text){
  if(!el||!text||el.dataset.parameterHelpReady)return;
  el.dataset.parameterHelpReady='true';el.title=text;
  const parent=el.closest('label');let label=el.getAttribute('aria-label')||parent?.childNodes[0]?.textContent?.trim()||el.id.replaceAll('-',' ');
  if(!label)label=el.id.replaceAll('-',' ');
  let help=parent?.querySelector('.parameter-help-inline,.setting-explanation');
  if(!help&&parent&&!parent.querySelector('small')){help=document.createElement('small');help.className='parameter-help-inline';help.textContent=text;parent.append(help);}
  if(help){if(!help.id)help.id='parameter-help-'+(el.id||el.dataset.setting);el.setAttribute('aria-describedby',[el.getAttribute('aria-describedby'),help.id].filter(Boolean).join(' '));}
  el.addEventListener('focus',()=>showHelp(el,label,text,'',150));el.addEventListener('pointerenter',()=>showHelp(el,label,text));el.addEventListener('blur',hideRibbonHint);el.addEventListener('pointerleave',hideRibbonHint);el.addEventListener('input',hideRibbonHint);
 }
 function scan(){
  for(const [id,text] of Object.entries(explanations))attach(document.getElementById(id),text);
  for(const [key,text] of Object.entries(paper))attach(document.querySelector('[data-setting="'+key+'"]'),text);
  document.querySelectorAll('.tab input[title],.tab select[title],dialog input[title],dialog select[title]').forEach(el=>attach(el,el.title));
 }
 for(const id of ['tab-structures','tab-analysis-tools','tab-section','tab-joints','tab-heatmap']){const section=document.getElementById(id);if(!section)continue;const label=document.createElement('label');label.className='parameter-help-toggle';label.innerHTML='<input type="checkbox" data-parameter-help-toggle> Show explanations beside settings';label.querySelector('input').onchange=e=>{enabled=e.target.checked;apply();};const heading=section.querySelector('.surface-intro,h2');if(heading)heading.after(label);else section.prepend(label);}
 const guide=document.createElement('dialog');guide.className='workflow-guide';guide.id='workflow-guide';guide.innerHTML='<div class="dialog-heading"><h2>Find the right tool</h2><button class="guide-close" aria-label="Close workflow guide">×</button></div><p>Select the scan or model in the left explorer, choose a tool in the ribbon, then review its settings and result. Yellow bulbs show visible items; grey bulbs hide them without deleting data.</p><div class="workflow-guide-grid"></div><p>Distances use metres unless a setting says mm. Display detail changes the view; full-density point selection and cleaning use the original retained points. Press Escape to finish or cancel picking. Ctrl+Z undoes workspace edits.</p>';
 const descriptions={Edit:'Select points, set a limit box, hide or remove points, then Undo if needed.',Registration:'Match survey targets, join scans and review registration residuals.',Clean:'Extract the tunnel with GuSAC, review local bolts or grow a connected patch.',Alignment:'Create or import the horizontal alignment. Draw its route, enter coordinates, edit heights or fit a centreline from scans.',Model:'Create 3D model from alignment: horizontal route → heights at chainages → theoretical profiles → chainage sections → 3D model. Saved models remain editable. Geometry tools are here too.',Meshing:'Create triangle surfaces, smooth or reduce them, and inspect gap repairs.',Analyse:'Compare clouds and meshes, visualise lining clusters and inspect sections.',Measure:'Save precise distances, angles, surface areas and valid solid volumes.',Report:'Generate a survey report or export data in the required format.'};
 for(const [tab,text] of Object.entries(descriptions)){const b=document.createElement('button');const title=document.createElement('strong'),description=document.createElement('span');title.textContent=tab;description.textContent=text;b.append(title,description);b.onclick=()=>{guide.close();window.TunnelRibbon.activate(tab);};guide.querySelector('.workflow-guide-grid').append(b);}guide.querySelector('.guide-close').onclick=()=>guide.close();document.body.append(guide);
 function openGuide(){hideRibbonHint();if(!guide.open)guide.showModal();}
 let scanQueued=false;const observer=new MutationObserver(()=>{if(scanQueued)return;scanQueued=true;queueMicrotask(()=>{scanQueued=false;scan();});});observer.observe(document.querySelector('.view-shell'),{subtree:true,childList:true});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')hideRibbonHint();});window.addEventListener('resize',hideRibbonHint);document.addEventListener('scroll',()=>{if(!owner||document.activeElement!==owner)hideRibbonHint();},true);
 window.ParameterHelp={toggle,openGuide,ribbonHint,hideRibbonHint};scan();apply();
})();
