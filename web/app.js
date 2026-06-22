var CV=document.getElementById('cv');
var ctx=CV.getContext('2d');
var DPR=Math.min(window.devicePixelRatio||1,2);
var W=0,H=0,frame=0;
var stage=0,zoomT=0,zoomTarget=0,slideT=0,slideTarget=0;
var activeGate=-1,activePlanet=-1;
var CX=0,CY=0,lastMaxR=200,lastRot=[0,0,0];

// Flask API served from the same origin by default.
// Override before app.js loads with: window.LOOP_API_ORIGIN = "https://host";
var LOOP_API_ORIGIN = (window.LOOP_API_ORIGIN || "").replace(/\/$/, "");
function loopApiUrl(path){
  return LOOP_API_ORIGIN + path;
}

// ============================================================
// ONTOLOGY-DRIVEN DATA MODEL
// Active workflow: PPK. Building blocks disabled in PPK are dimmed
// but still rendered for transparency.
// ============================================================
var WORKFLOW='PPK';

// Helper: build an indicator. Grades are arrays of {label, range, points, flag?}.
// `current` is the index of the active grade band based on the demo value.
function ind(spec){return spec;}

// =============================================================
// UNIVERSE 1 -- CAPTURE
// 4 operational subsystems + 1 aggregator. Weight in OJS: 20%.
// =============================================================
var SUB_CAPTURE_DRONE={
  id:'sub-drone', name:'Drone', short:'Drone',
  desc:'Captures images, records GNSS, executes the mission.',
  score:91, workflowOK:true,
  blocks:[
    {name:'Image Capture', score:88, workflowOK:true,
     desc:'Quality of the imagery delivered by the drone -- validity, geotagging, overlap, format, exposure, and calibration match.',
     weightInSub:0.40,
     ruleNotes:['If image validity drops below 30 points, the entire image capture score collapses to zero -- no recoverable survey data.'],
     indicators:[
      {name:'Image Validity', value:'98%', valuePoints:88, weight:0.28,
       desc:'Share of images that are not corrupted, blurred beyond recognition, or otherwise unreadable.',
       sources:['Drone provenance log','Image quality log'],
       grades:[
        {l:'Excellent', r:'99% or more', s:100},
        {l:'Strong',    r:'97% or more', s:88,  current:true},
        {l:'Acceptable',r:'94% or more', s:72},
        {l:'Marginal',  r:'90% or more', s:55},
        {l:'Critical',  r:'less than 90%',s:20}
       ],
       rec:'Validity sits in the Strong band. Continue current capture protocol.',
       alert:null},
      {name:'Image Geotagging', value:'100%', valuePoints:100, weight:0.22,
       desc:'Share of images that arrived with embedded GPS coordinates in EXIF metadata.',
       sources:['Drone provenance log','Image EXIF metadata'],
       grades:[
        {l:'Excellent', r:'99% or more', s:100, current:true},
        {l:'Strong',    r:'97% or more', s:88},
        {l:'Acceptable',r:'93% or more', s:72},
        {l:'Marginal',  r:'88% or more', s:55},
        {l:'Critical',  r:'less than 85%',s:20}
       ],
       rec:'Every image carries a geotag. PPK refinement can proceed without gap-filling.',
       alert:null},
      {name:'Image Overlap', value:'72%', valuePoints:88, weight:0.15,
       desc:'Lower of forward and side overlap measured across the survey. Drives reconstruction quality.',
       sources:['Drone provenance log','Mission plan'],
       grades:[
        {l:'Excellent', r:'70% or more', s:100},
        {l:'Strong',    r:'60% or more', s:88, current:true},
        {l:'Acceptable',r:'50% or more', s:72},
        {l:'Marginal',  r:'40% or more', s:50},
        {l:'Critical',  r:'less than 40%',s:20, flag:'Insufficient overlap'}
       ],
       rec:'Overlap meets the Strong band. Consider raising to 80% on dense-feature areas.',
       alert:null},
      {name:'Image Format', value:'JPG consistent', valuePoints:75, weight:0.10,
       desc:'Whether images were captured in a consistent format suitable for reconstruction.',
       sources:['Drone provenance log','Image EXIF metadata'],
       grades:[
        {l:'Raw',     r:'DNG or RAW',     s:100},
        {l:'JPG',     r:'JPG consistent', s:75, current:true},
        {l:'Mixed',   r:'Mixed DNG/JPG',  s:55, flag:'Mixed image format'}
       ],
       rec:'JPG-only is acceptable for survey work. Switch to RAW only if radiometric work is planned.',
       alert:null},
      {name:'Exposure Consistency', value:'CV 0.08', valuePoints:88, weight:0.07,
       desc:'How stable exposure stayed across the mission. High variation produces visible seams.',
       sources:['Drone provenance log','Image EXIF metadata'],
       grades:[
        {l:'Tight',    r:'CV below 0.05', s:100},
        {l:'Stable',   r:'CV below 0.10', s:88, current:true},
        {l:'Variable', r:'CV below 0.20', s:72},
        {l:'Loose',    r:'CV below 0.35', s:50},
        {l:'Erratic',  r:'CV 0.35 or more', s:25, flag:'High exposure variation'}
       ],
       rec:'Exposure held stable. No mosaic seam concerns.',
       alert:null},
      {name:'Calibration Match', value:'Both match', valuePoints:100, weight:0.11,
       desc:'Whether the camera used in the field matches the calibration file used in processing.',
       sources:['Camera calibration file','Drone provenance log'],
       grades:[
        {l:'Both match',  r:'Make and model match', s:100, current:true},
        {l:'Make only',   r:'Only make matches',    s:60},
        {l:'No match',    r:'Neither matches',      s:20}
       ],
       rec:'Calibration file matches the field camera. Self-calibration will be reliable.',
       alert:null}
     ]},

    {name:'Mission Execution', score:96, workflowOK:true,
     desc:'How well the drone followed the planned mission -- altitude, speed, waypoint completion, takeoff buffer, weather, and terrain follow.',
     weightInSub:0.20,
     ruleNotes:[],
     indicators:[
      {name:'Waypoints Reached', value:'100%', valuePoints:100, weight:0.20,
       desc:'Share of planned waypoints actually flown.',
       sources:['Mission plan','Flight log'],
       grades:[
        {l:'Complete',     r:'100%',     s:100, current:true},
        {l:'Near-complete',r:'95% or more',s:80},
        {l:'Partial',      r:'80% or more',s:50},
        {l:'Aborted',      r:'less than 80%',s:15}
       ],
       rec:'Mission executed exactly as planned.', alert:null},
      {name:'Altitude Stability', value:'2.1 m sigma', valuePoints:90, weight:0.18,
       desc:'How well the drone held its target altitude (terrain-follow mode tracks the ground).',
       sources:['Flight log','Mission plan'],
       grades:[
        {l:'Tight',   r:'sigma below 2 m',  s:100},
        {l:'Stable',  r:'sigma below 4 m',  s:90, current:true},
        {l:'Variable',r:'sigma below 8 m',  s:60},
        {l:'Erratic', r:'sigma 8 m or more',s:25}
       ],
       rec:'Altitude held well within tolerance.', alert:null},
      {name:'Flight Speed', value:'5.8 m/s', valuePoints:100, weight:0.15,
       desc:'Whether the drone maintained the recommended cruise speed for sharp imagery.',
       sources:['Flight log'],
       grades:[
        {l:'Optimal',   r:'4 to 7 m/s',  s:100, current:true},
        {l:'Acceptable',r:'7 to 10 m/s', s:80},
        {l:'Too fast',  r:'over 10 m/s', s:45, flag:'Motion blur risk'}
       ],
       rec:'Cruise speed in the optimal band; minimal motion-blur risk.', alert:null},
      {name:'GNSS Static Buffer', value:'18 s', valuePoints:55, weight:0.15,
       desc:'How long the drone held a static GNSS lock before takeoff. Drives PPK initialisation quality.',
       sources:['Flight log','Drone provenance log'],
       grades:[
        {l:'Generous',     r:'180 s or more', s:100},
        {l:'Sufficient',   r:'120 s or more', s:85},
        {l:'Short',        r:'60 s or more',  s:55, current:true},
        {l:'Insufficient', r:'less than 60 s',s:25, flag:'Inadequate static buffer'}
       ],
       rec:'Hold a 120-second static buffer before takeoff next mission to improve PPK initialisation.',
       alert:'Static GNSS buffer is below the recommended 120-second target.'},
      {name:'Weather Conditions', value:'Clear', valuePoints:100, weight:0.12,
       desc:'Wind, cloud cover, and visibility during the survey window.',
       sources:['Weather log','Flight log'],
       grades:[
        {l:'Clear',     r:'Wind below 8 m/s, clear sky', s:100, current:true},
        {l:'Acceptable',r:'Wind below 12 m/s or partly cloudy', s:80},
        {l:'Marginal',  r:'Wind below 15 m/s or overcast', s:55},
        {l:'Adverse',   r:'Wind 15 m/s or higher, or precipitation', s:20}
       ],
       rec:'Conditions were ideal.', alert:null},
      {name:'Battery Margin', value:'34%', valuePoints:100, weight:0.10,
       desc:'Battery remaining at end of mission. Low margins risk mission abort.',
       sources:['Flight log'],
       grades:[
        {l:'Comfortable',r:'25% or more', s:100, current:true},
        {l:'Adequate',   r:'15% or more', s:75},
        {l:'Tight',      r:'8% or more',  s:45},
        {l:'Critical',   r:'less than 8%',s:15, flag:'Battery margin too low'}
       ],
       rec:'Comfortable margin on landing.', alert:null},
      {name:'Terrain Follow', value:'Active', valuePoints:100, weight:0.10,
       desc:'Whether terrain-follow mode was enabled so altitude tracked the ground rather than mean sea level.',
       sources:['Mission plan','Flight log'],
       grades:[
        {l:'Active',  r:'Enabled',       s:100, current:true},
        {l:'Disabled',r:'Flat altitude', s:60}
       ],
       rec:'Terrain follow held GSD within tolerance across the AOI.', alert:null}
     ]},

    {name:'Rover GNSS Quality', score:92, workflowOK:true,
     desc:'Quality of the GNSS observations recorded on the drone (the rover) during flight.',
     weightInSub:0.30,
     ruleNotes:['Rover GNSS is disabled when the workflow has no GNSS correction.'],
     indicators:[
      {name:'RINEX Recording', value:'Continuous', valuePoints:100, weight:0.25,
       desc:'Whether the rover wrote continuous RINEX observations across the mission.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'Continuous',  r:'No gaps',     s:100, current:true},
        {l:'Minor gaps',  r:'1 to 2 gaps under 5s', s:75},
        {l:'Major gaps',  r:'Any gap over 5s', s:30, flag:'Rover RINEX gap'}
       ],
       rec:'Continuous rover record; PPK can use the whole flight.', alert:null},
      {name:'Recording Frequency', value:'10 Hz', valuePoints:100, weight:0.15,
       desc:'Rate at which the rover sampled GNSS. Higher rates support better PPK interpolation.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'High',    r:'10 Hz or more', s:100, current:true},
        {l:'Adequate',r:'5 Hz',          s:80},
        {l:'Low',     r:'1 Hz',          s:50}
       ],
       rec:'Sampling rate supports tight PPK trajectory.', alert:null},
      {name:'Signal Strength', value:'46 dBHz', valuePoints:90, weight:0.20,
       desc:'Average satellite signal-to-noise ratio during recording.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'Strong',  r:'45 dBHz or more', s:100},
        {l:'Good',    r:'40 dBHz or more', s:90, current:true},
        {l:'Marginal',r:'35 dBHz or more', s:55},
        {l:'Weak',    r:'less than 35 dBHz', s:25, flag:'Weak rover signal'}
       ],
       rec:'Signal in the Good band; PPK will resolve cleanly.', alert:null},
      {name:'PDOP', value:'1.8', valuePoints:100, weight:0.15,
       desc:'Position dilution of precision. Lower means better satellite geometry.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'Excellent',r:'below 2',    s:100, current:true},
        {l:'Good',     r:'below 3',    s:85},
        {l:'Marginal', r:'below 5',    s:55},
        {l:'Poor',     r:'5 or higher',s:25}
       ],
       rec:'Geometry was strong throughout the mission.', alert:null},
      {name:'Multipath', value:'Low', valuePoints:90, weight:0.15,
       desc:'Estimate of multipath contamination based on residuals and environment.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'Low',      r:'Few outliers',  s:100},
        {l:'Acceptable',r:'Some outliers',s:90, current:true},
        {l:'Elevated', r:'Many outliers', s:55},
        {l:'Severe',   r:'Dominant',      s:20, flag:'Severe multipath'}
       ],
       rec:'Multipath is acceptable for survey work.', alert:null},
      {name:'Session Integrity', value:'Clean', valuePoints:100, weight:0.10,
       desc:'Whether the session ended cleanly with no truncation or corruption.',
       sources:['Rover RINEX file'],
       grades:[
        {l:'Clean',    r:'Closed properly',     s:100, current:true},
        {l:'Truncated',r:'Ended without close', s:55},
        {l:'Corrupted',r:'Header or record damage',s:15, flag:'Session corrupted'}
       ],
       rec:'Session closed cleanly.', alert:null}
     ]},

    // RTK fix score -- DISABLED in PPK workflow
    {name:'RTK Fix Rate', score:null, workflowOK:false,
     desc:'Real-time integer-fix rate during the flight. Only relevant when the survey runs in RTK mode.',
     weightInSub:0.00,
     ruleNotes:['This building block is not used in PPK workflows. PPK refines positions post-flight rather than relying on real-time fixes.'],
     indicators:[]}
  ]
};

// =============================================================
var SUB_CAPTURE_BASE={
  id:'sub-base', name:'Base Station', short:'Base',
  desc:'GNSS reference recorder. Provides the static position that every PPK-corrected drone coordinate depends on.',
  score:94, workflowOK:true,
  blocks:[
    {name:'RINEX Recording', score:96, workflowOK:true,
     desc:'How well the base station recorded GNSS observations during the flight.',
     weightInSub:0.30,
     ruleNotes:['If the base did not record during flight, the entire RINEX recording score is zero.'],
     indicators:[
      {name:'Flight Coverage', value:'100%', valuePoints:100, weight:0.40,
       desc:'Share of flight time during which the base was recording.',
       sources:['Base RINEX file','Flight log'],
       grades:[
        {l:'Full',     r:'100%', s:100, current:true},
        {l:'Near-full',r:'95% or more',s:80},
        {l:'Partial',  r:'80% or more',s:50},
        {l:'Gap',      r:'less than 80%',s:0, flag:'Base RINEX flight gap'}
       ],
       rec:'Base recorded across the full flight window.', alert:null},
      {name:'Signal Strength', value:'48 dBHz', valuePoints:100, weight:0.25,
       desc:'Average satellite signal strength at the base.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Strong',  r:'45 dBHz or more',s:100, current:true},
        {l:'Good',    r:'40 dBHz or more',s:80},
        {l:'Marginal',r:'35 dBHz or more',s:50},
        {l:'Weak',    r:'less than 35 dBHz',s:20}
       ],
       rec:'Base sky view is excellent.', alert:null},
      {name:'Recording Frequency', value:'1 Hz', valuePoints:90, weight:0.20,
       desc:'Sampling rate of base observations. Must align with rover for PPK.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Matched',  r:'Matches rover rate',s:100},
        {l:'Compatible',r:'Rover rate is a multiple',s:90, current:true},
        {l:'Mismatched',r:'Incompatible rates',s:30, flag:'Frequency mismatch'}
       ],
       rec:'Rates are compatible; PPK will downsample cleanly.', alert:null},
      {name:'Continuity', value:'Continuous', valuePoints:100, weight:0.15,
       desc:'Whether the base produced an unbroken record.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Continuous',r:'No gaps',s:100, current:true},
        {l:'Brief gaps',r:'Under 30 s',s:75},
        {l:'Long gaps', r:'30 s or more',s:30, flag:'Base recording gap'}
       ],
       rec:'Continuous record supports PPK across the full flight.', alert:null}
     ]},

    {name:'Session Quality', score:92, workflowOK:true,
     desc:'Acquisition health, geometry, and multipath at the base across the recording session.',
     weightInSub:0.25,
     ruleNotes:[],
     indicators:[
      {name:'Acquisition Time', value:'4.5 hr', valuePoints:100, weight:0.30,
       desc:'Total length of the base recording session.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Generous',r:'4 hr or more',s:100, current:true},
        {l:'Adequate',r:'2 hr or more',s:80},
        {l:'Short',   r:'1 hr or more',s:55},
        {l:'Too short',r:'less than 1 hr',s:25}
       ],
       rec:'Session length supports robust CORS post-processing.', alert:null},
      {name:'PDOP', value:'1.6', valuePoints:100, weight:0.30,
       desc:'Average geometry at the base. Lower is better.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Excellent',r:'below 2',s:100, current:true},
        {l:'Good',     r:'below 3',s:85},
        {l:'Marginal', r:'below 5',s:50},
        {l:'Poor',     r:'5 or higher',s:20}
       ],
       rec:'Strong satellite geometry throughout.', alert:null},
      {name:'Multipath', value:'Low', valuePoints:90, weight:0.20,
       desc:'Multipath contamination at the base location.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Low',      r:'Clean site',  s:100},
        {l:'Acceptable',r:'Mild',       s:90, current:true},
        {l:'Elevated', r:'Notable',     s:50},
        {l:'Severe',   r:'Dominant',    s:15, flag:'Site multipath'}
       ],
       rec:'Multipath is acceptable for survey-grade base positioning.', alert:null},
      {name:'Session Integrity', value:'Clean', valuePoints:100, weight:0.20,
       desc:'Whether the session opened and closed without corruption.',
       sources:['Base RINEX file'],
       grades:[
        {l:'Clean',    r:'No corruption',s:100, current:true},
        {l:'Truncated',r:'Ended early',  s:50},
        {l:'Corrupted',r:'Header damage',s:15}
       ],
       rec:'Session is clean.', alert:null}
     ]},

    {name:'Antenna Setup', score:88, workflowOK:true,
     desc:'Physical setup of the antenna on the tripod -- height, orientation, and type matching.',
     weightInSub:0.15,
     ruleNotes:['If antenna height was not documented, the antenna height score collapses to zero. Vertical accuracy of every position downstream is unverifiable.'],
     indicators:[
      {name:'Antenna Height', value:'1.652 m', valuePoints:100, weight:0.55,
       desc:'Whether the antenna height above the ground mark was documented.',
       sources:['Antenna setup record'],
       grades:[
        {l:'Documented',r:'Recorded value',s:100, current:true},
        {l:'Missing',   r:'Not recorded',  s:0,   flag:'Antenna height missing'}
       ],
       rec:'Height documented; vertical positioning is verifiable.', alert:null},
      {name:'Setup Verification', value:'Photo verified', valuePoints:75, weight:0.30,
       desc:'Whether the setup was independently verified (photo, second operator, or instrument check).',
       sources:['Antenna setup record','Field photos'],
       grades:[
        {l:'Photo and instrument',r:'Two methods',s:100},
        {l:'Photo verified',      r:'Single photo',s:75, current:true},
        {l:'Operator only',       r:'No verification',s:40, flag:'Setup not verified'}
       ],
       rec:'Photo verification on file. Add an instrument check for sub-cm work.', alert:null},
      {name:'Antenna Type Match', value:'Match', valuePoints:100, weight:0.15,
       desc:'Whether the antenna type used in the field matches the calibration record.',
       sources:['Antenna setup record','Antenna calibration database'],
       grades:[
        {l:'Match',   r:'Same model',     s:100, current:true},
        {l:'Mismatch',r:'Different model',s:20,  flag:'Antenna type mismatch'},
        {l:'Unknown', r:'Type not in record',s:50}
       ],
       rec:'Antenna matches the calibration used.', alert:null}
     ]},

    // base_rtk_broadcast -- DISABLED in PPK
    {name:'RTK Broadcast', score:null, workflowOK:false,
     desc:'Real-time correction broadcast from the base to the rover. Only used in RTK workflows.',
     weightInSub:0.00,
     ruleNotes:['Not applicable in PPK. PPK reads observations and computes corrections after the flight.'],
     indicators:[]},

    {name:'Base Position Quality', score:96, workflowOK:true,
     desc:'How well the base station\'s own position is known. Resolved after the field session via CORS post-processing.',
     weightInSub:0.30,
     ruleNotes:['This building block resolves in Stage 2 after the field work.'],
     indicators:[
      {name:'Position Accuracy', value:'0.012 m', valuePoints:100, weight:0.30,
       desc:'Stated horizontal and vertical accuracy of the established base position.',
       sources:['Stage 2 known-point report'],
       grades:[
        {l:'Survey',     r:'2 cm or better',  s:100, current:true},
        {l:'Engineering',r:'5 cm or better',  s:80},
        {l:'Mapping',    r:'10 cm or better', s:55},
        {l:'Unknown',    r:'No accuracy stated',s:55, flag:'Accuracy not stated'}
       ],
       rec:'Base position is survey-grade.', alert:null},
      {name:'Position Source', value:'CORS processed', valuePoints:100, weight:0.20,
       desc:'How the base position was established.',
       sources:['Stage 2 known-point report'],
       grades:[
        {l:'CORS processed',     r:'TBC network adjustment',s:100, current:true},
        {l:'Customer benchmark', r:'Provided directly',     s:75},
        {l:'Self-occupied',      r:'Self-survey',           s:55},
        {l:'Unknown',            r:'Source not stated',     s:20}
       ],
       rec:'CORS processing provides the strongest base position.', alert:null},
      {name:'CORS Processing Rigor', value:'Long baseline', valuePoints:90, weight:0.15,
       desc:'Quality of the CORS network adjustment used.',
       sources:['CORS network report'],
       grades:[
        {l:'Network',  r:'Multi-station, weighted',s:100},
        {l:'Robust',   r:'Multi-station, simple',  s:90, current:true},
        {l:'Single',   r:'Single CORS station',    s:60}
       ],
       rec:'Network adjustment looks healthy.', alert:null},
      {name:'CORS Station Quality', value:'Class A', valuePoints:100, weight:0.15,
       desc:'Health of the CORS stations used in the network adjustment.',
       sources:['CORS network report'],
       grades:[
        {l:'Class A',r:'Tier-1 stations',s:100, current:true},
        {l:'Class B',r:'Mixed tiers',    s:75},
        {l:'Class C',r:'Lower-tier',     s:45}
       ],
       rec:'All anchor stations are Class A.', alert:null},
      {name:'Ambiguity Resolution', value:'Fixed', valuePoints:100, weight:0.15,
       desc:'Whether integer ambiguities were resolved in the CORS solution.',
       sources:['CORS network report'],
       grades:[
        {l:'Fixed', r:'All ambiguities fixed',s:100, current:true},
        {l:'Partial',r:'Some fixed',          s:70},
        {l:'Float', r:'No fixing',            s:40}
       ],
       rec:'Full ambiguity resolution; position is reliable.', alert:null},
      {name:'Operator Override', value:'None', valuePoints:100, weight:0.10,
       desc:'Whether the operator manually overrode any CORS solution parameters.',
       sources:['Stage 2 known-point report'],
       grades:[
        {l:'None',       r:'Default solution', s:100, current:true},
        {l:'Documented', r:'Override with reason',s:75},
        {l:'Undocumented',r:'Override no reason',s:35, flag:'Unexplained override'}
       ],
       rec:'No overrides applied.', alert:null},
      {name:'Ionospheric Correction', value:'Applied', valuePoints:100, weight:0.05,
       desc:'Whether ionospheric modelling was applied in the CORS solution.',
       sources:['CORS network report'],
       grades:[
        {l:'Applied',    r:'Klobuchar or better',s:100, current:true},
        {l:'Not applied',r:'Disabled',           s:60}
       ],
       rec:'Ionospheric correction reduces position bias.', alert:null},
      {name:'Reference Frame Consistency', value:'ITRF2014', valuePoints:100, weight:0.05,
       desc:'Whether the reference frame is consistent with downstream products.',
       sources:['CORS network report','Coordinate system'],
       grades:[
        {l:'Consistent',  r:'Matches output frame',s:100, current:true},
        {l:'Convertible', r:'Different but convertible',s:80},
        {l:'Inconsistent',r:'Frame mismatch',     s:30, flag:'Reference frame mismatch'}
       ],
       rec:'Frame matches the project coordinate system.', alert:null}
     ]}
  ]
};

// =============================================================
var SUB_CAPTURE_GCP={
  id:'sub-gcp', name:'Control Point Network', short:'Control Point',
  desc:'Ground control points -- surveyed markers that anchor the photogrammetric reconstruction to absolute coordinates.',
  score:78, workflowOK:true,
  blocks:[
    {name:'Device Recording', score:88, workflowOK:true,
     desc:'How well the Control Point rover recorded GNSS at each point.',
     weightInSub:0.25,
     ruleNotes:[],
     indicators:[
      {name:'RINEX Completeness', value:'12 of 12', valuePoints:100, weight:0.40,
       desc:'Share of Control Points that produced a usable RINEX observation file.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Complete',  r:'All points',     s:100, current:true},
        {l:'Most',      r:'90% or more',    s:80},
        {l:'Partial',   r:'70% or more',    s:50},
        {l:'Incomplete',r:'less than 70%',  s:15, flag:'Missing Control Point recordings'}
       ],
       rec:'Every Control Point has a recording.', alert:null},
      {name:'Occupation Duration', value:'18 min mean', valuePoints:88, weight:0.30,
       desc:'Mean duration of each Control Point occupation.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Long',     r:'20 min or more', s:100},
        {l:'Sufficient',r:'15 min or more',s:88, current:true},
        {l:'Short',    r:'10 min or more', s:60},
        {l:'Too short',r:'less than 10 min',s:30}
       ],
       rec:'Occupation lengths support clean PPP solutions.', alert:null},
      {name:'Signal Strength', value:'47 dBHz', valuePoints:100, weight:0.15,
       desc:'Average Control Point rover signal-to-noise ratio.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Strong',  r:'45 dBHz or more',s:100, current:true},
        {l:'Good',    r:'40 dBHz or more',s:85},
        {l:'Marginal',r:'35 dBHz or more',s:50},
        {l:'Weak',    r:'less than 35 dBHz',s:20}
       ],
       rec:'Strong signal at all Control Points.', alert:null},
      {name:'PDOP', value:'1.9', valuePoints:100, weight:0.15,
       desc:'Average geometry at Control Point occupations.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Excellent',r:'below 2',     s:100, current:true},
        {l:'Good',     r:'below 3',     s:85},
        {l:'Marginal', r:'below 5',     s:50},
        {l:'Poor',     r:'5 or higher', s:20}
       ],
       rec:'Geometry at Control Points was strong.', alert:null}
     ]},

    {name:'Session Quality', score:90, workflowOK:true,
     desc:'Acquisition health across the Control Point recording session as a whole.',
     weightInSub:0.20,
     ruleNotes:[],
     indicators:[
      {name:'Acquisition Window', value:'08:30 to 11:00', valuePoints:100, weight:0.25,
       desc:'Whether the Control Point campaign happened in a clean satellite window.',
       sources:['Control Point RINEX files','Site log'],
       grades:[
        {l:'Optimal',   r:'Strong window',s:100, current:true},
        {l:'Acceptable',r:'Average window',s:80},
        {l:'Poor',      r:'Weak window',  s:45}
       ],
       rec:'Campaign captured a strong satellite window.', alert:null},
      {name:'Multipath', value:'Acceptable', valuePoints:85, weight:0.25,
       desc:'Average multipath contamination across Control Point sites.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Low',       r:'Open sites',     s:100},
        {l:'Acceptable',r:'Mild blockage',  s:85, current:true},
        {l:'Elevated',  r:'Notable',        s:50},
        {l:'Severe',    r:'Dominant',       s:15}
       ],
       rec:'Multipath acceptable across the network.', alert:null},
      {name:'Operator Consistency', value:'Same operator', valuePoints:100, weight:0.25,
       desc:'Whether one operator collected all Control Points (consistency reduces systematic bias).',
       sources:['Field log'],
       grades:[
        {l:'Single',  r:'One operator',  s:100, current:true},
        {l:'Multiple',r:'Multiple operators',s:75}
       ],
       rec:'Single operator across the campaign.', alert:null},
      {name:'Session Integrity', value:'Clean', valuePoints:100, weight:0.25,
       desc:'Whether the session ended without corruption or truncation.',
       sources:['Control Point RINEX files'],
       grades:[
        {l:'Clean',    r:'No corruption',s:100, current:true},
        {l:'Truncated',r:'Ended early',  s:50},
        {l:'Corrupted',r:'Damaged files',s:15}
       ],
       rec:'Session is clean.', alert:null}
     ]},

    {name:'Network Layout', score:64, workflowOK:true,
     desc:'How well the Control Points are distributed across the area of interest.',
     weightInSub:0.30,
     ruleNotes:[],
     indicators:[
      {name:'Control Point Count', value:'12', valuePoints:88, weight:0.25,
       desc:'Number of surveyed Control Points in the AOI.',
       sources:['Control Point layout record'],
       grades:[
        {l:'Ample',       r:'15 or more',  s:100},
        {l:'Adequate',    r:'8 to 14',     s:88, current:true},
        {l:'Sparse',      r:'5 to 7',      s:60},
        {l:'Insufficient',r:'less than 5', s:25}
       ],
       rec:'Count supports mapping-grade; add 3 more for survey-grade certification.', alert:null},
      {name:'Spatial Distribution', value:'NE under-served', valuePoints:45, weight:0.30,
       desc:'How evenly Control Points are distributed across the AOI quadrants.',
       sources:['Control Point layout record','AOI boundary'],
       grades:[
        {l:'Uniform',   r:'All quadrants covered evenly',s:100},
        {l:'Adequate',  r:'Each quadrant covered',      s:75},
        {l:'Skewed',    r:'One quadrant under-served',  s:45, current:true},
        {l:'Clustered', r:'Most Control Points in one area',      s:20, flag:'Control Point clustering'}
       ],
       rec:'Add 2 Control Points in the NE quadrant before certifying survey-grade.',
       alert:'NE quadrant has only 1 Control Point within 200 m radius.'},
      {name:'Edge Coverage', value:'Partial', valuePoints:55, weight:0.25,
       desc:'Whether Control Points span the AOI boundary edges (critical for reconstruction edge accuracy).',
       sources:['Control Point layout record','AOI boundary'],
       grades:[
        {l:'Full edge',  r:'All edges covered',s:100},
        {l:'Most edges', r:'Most covered',     s:75},
        {l:'Partial',    r:'Half covered',     s:55, current:true},
        {l:'Centre only',r:'Edges bare',       s:25, flag:'Edge Control Points missing'}
       ],
       rec:'Add edge Control Points to lock reconstruction at the AOI boundary.', alert:null},
      {name:'Vertical Spread', value:'Limited', valuePoints:55, weight:0.20,
       desc:'Whether Control Points span the AOI elevation range, not just one elevation band.',
       sources:['Control Point layout record'],
       grades:[
        {l:'Full spread',r:'Across all elevations',s:100},
        {l:'Adequate',   r:'Two elevation bands',  s:80},
        {l:'Limited',    r:'One band',             s:55, current:true},
        {l:'Single',     r:'All at one elevation', s:20}
       ],
       rec:'Include Control Points at higher and lower elevations to improve vertical reconstruction.', alert:null}
     ]},

    {name:'Coordinate Quality', score:82, workflowOK:true,
     desc:'Quality of the final coordinates assigned to each Control Point. Resolved in Stage 2.',
     weightInSub:0.25,
     ruleNotes:['Resolves after Stage 2 Control Point coordinate processing.'],
     indicators:[
      {name:'Mean Residual', value:'9.8 mm', valuePoints:100, weight:0.40,
       desc:'Mean residual across all Control Points after bundle adjustment.',
       sources:['Control Point coordinate file','Stage 2 report'],
       grades:[
        {l:'Survey-grade',r:'10 mm or less',s:100, current:true},
        {l:'Engineering', r:'15 mm or less',s:78},
        {l:'Mapping',     r:'25 mm or less',s:55},
        {l:'Reject',      r:'over 25 mm',   s:15}
       ],
       rec:'Mean residual sits at the survey threshold; monitor individual outliers.', alert:null},
      {name:'Worst Residual', value:'14 mm', valuePoints:78, weight:0.30,
       desc:'Largest residual on any individual Control Point.',
       sources:['Control Point coordinate file','Stage 2 report'],
       grades:[
        {l:'Survey-grade',r:'10 mm or less',s:100},
        {l:'Engineering', r:'15 mm or less',s:78, current:true},
        {l:'Mapping',     r:'25 mm or less',s:55},
        {l:'Reject',      r:'over 25 mm',   s:15}
       ],
       rec:'One Control Point exceeds the 10 mm survey threshold. Relocate or remeasure.',
       alert:'Control Point-2 residual at 14 mm. Above survey threshold.'},
      {name:'Coordinate Source', value:'PPP processed', valuePoints:90, weight:0.30,
       desc:'How Control Point coordinates were established.',
       sources:['Control Point coordinate file','Stage 2 report'],
       grades:[
        {l:'CORS processed',  r:'TBC network',          s:100},
        {l:'PPP processed',   r:'Precise point positioning',s:90, current:true},
        {l:'Customer-provided',r:'Direct values',       s:75},
        {l:'Self-survey',     r:'Single occupation',    s:50}
       ],
       rec:'PPP gives strong absolute positioning.', alert:null}
     ]}
  ]
};

// =============================================================
var SUB_CAPTURE_PREPROC={
  id:'sub-preproc', name:'Pre-Processing', short:'Pre-Proc',
  desc:'Stage 2 processing -- known-point establishment, PPK solution, Control Point coordinates. Bridges field capture to ODM reconstruction.',
  score:90, workflowOK:true,
  blocks:[
    {name:'PPK Solution Quality', score:90, workflowOK:true,
     desc:'Quality of the post-processed kinematic solution that produces refined drone positions.',
     weightInSub:1.00,
     ruleNotes:['Active in PPK workflows. Disabled when no GNSS correction is applied.'],
     indicators:[
      {name:'Solution Type', value:'Fixed', valuePoints:100, weight:0.40,
       desc:'Whether the PPK solution resolved as a fixed (integer ambiguity) solution.',
       sources:['PPK trajectory file','PPK report'],
       grades:[
        {l:'Fixed',  r:'Integer ambiguities resolved',s:100, current:true},
        {l:'Float',  r:'Partial resolution',         s:55},
        {l:'No solution',r:'PPK failed',             s:0,   flag:'PPK failed'}
       ],
       rec:'Fixed solution -- positions are sub-cm precision.', alert:null},
      {name:'Processing Rigor', value:'Robust', valuePoints:90, weight:0.25,
       desc:'Quality settings used in the PPK engine.',
       sources:['PPK report'],
       grades:[
        {l:'Strict',  r:'Tight thresholds',     s:100},
        {l:'Robust',  r:'Default thresholds',   s:90, current:true},
        {l:'Loose',   r:'Relaxed thresholds',   s:60},
        {l:'Aggressive',r:'Very relaxed',       s:30, flag:'Loose PPK'}
       ],
       rec:'Default thresholds are appropriate for this baseline.', alert:null},
      {name:'Baseline Quality', value:'1.2 km', valuePoints:100, weight:0.15,
       desc:'Distance between base and rover. Shorter baselines give tighter solutions.',
       sources:['PPK report'],
       grades:[
        {l:'Tight',    r:'5 km or less',  s:100, current:true},
        {l:'Acceptable',r:'15 km or less',s:80},
        {l:'Long',     r:'30 km or less', s:55},
        {l:'Excessive',r:'over 30 km',    s:25}
       ],
       rec:'Baseline well within PPK tolerance.', alert:null},
      {name:'Geotagging Completeness', value:'100%', valuePoints:100, weight:0.15,
       desc:'Share of images that received a PPK-refined coordinate.',
       sources:['PPK trajectory file','Image EXIF metadata'],
       grades:[
        {l:'Complete',  r:'100%',     s:100, current:true},
        {l:'Near-complete',r:'95% or more',s:80},
        {l:'Partial',   r:'80% or more',s:50},
        {l:'Incomplete',r:'less than 80%',s:15}
       ],
       rec:'Every image carries a refined coordinate.', alert:null},
      {name:'Operator Override', value:'None', valuePoints:100, weight:0.05,
       desc:'Whether the operator overrode default PPK settings.',
       sources:['PPK report'],
       grades:[
        {l:'None',        r:'Default settings',s:100, current:true},
        {l:'Documented',  r:'Override with note',s:80},
        {l:'Undocumented',r:'Override no note',s:40, flag:'Unexplained override'}
       ],
       rec:'No overrides applied.', alert:null}
     ]}
  ]
};

// =============================================================
// UNIVERSE 2 -- PROCESSING
// 3 subsystems + 1 aggregator. Weight in OJS: 35%.
// =============================================================
var SUB_PROC_RECON={
  id:'sub-recon', name:'Reconstruction', short:'Recon',
  desc:'Image alignment, feature matching, and georeferencing -- the photogrammetric core.',
  score:90, workflowOK:true,
  blocks:[
    {name:'Control Point Marking', score:92, workflowOK:true,
     desc:'How well Control Points were marked in images during reconstruction.',
     weightInSub:0.30,
     ruleNotes:[],
     indicators:[
      {name:'Marked Control Point Count', value:'12 of 12', valuePoints:100, weight:0.30,
       desc:'Share of provided Control Points that were marked in images.',
       sources:['Control Point marking file'],
       grades:[
        {l:'All',     r:'100%',    s:100, current:true},
        {l:'Most',    r:'90% or more',s:80},
        {l:'Partial', r:'70% or more',s:50},
        {l:'Few',     r:'less than 70%',s:20}
       ],
       rec:'Every Control Point was marked.', alert:null},
      {name:'Markings Per Control Point', value:'8 mean', valuePoints:100, weight:0.25,
       desc:'Mean number of images each Control Point was marked in.',
       sources:['Control Point marking file'],
       grades:[
        {l:'Robust',    r:'8 or more',  s:100, current:true},
        {l:'Adequate',  r:'5 to 7',     s:80},
        {l:'Sparse',    r:'3 to 4',     s:55},
        {l:'Insufficient',r:'less than 3',s:25}
       ],
       rec:'Marking density supports tight bundle adjustment.', alert:null},
      {name:'Marking Accuracy', value:'0.6 px mean', valuePoints:85, weight:0.25,
       desc:'Mean pixel error of Control Point markings.',
       sources:['Control Point marking file','Reconstruction report'],
       grades:[
        {l:'Tight',     r:'below 0.5 px',s:100},
        {l:'Acceptable',r:'below 1 px',  s:85, current:true},
        {l:'Loose',     r:'below 2 px',  s:55},
        {l:'Poor',      r:'2 px or more',s:20, flag:'Loose marking'}
       ],
       rec:'Marking accuracy is in the Acceptable band.', alert:null},
      {name:'Distribution', value:'Even', valuePoints:95, weight:0.10,
       desc:'How well markings are distributed across image space.',
       sources:['Control Point marking file'],
       grades:[
        {l:'Even',     r:'Across all images',s:100},
        {l:'Acceptable',r:'Most images',     s:80, current:true},
        {l:'Clustered',r:'Few images',       s:40}
       ],
       rec:'Markings span the image set evenly.', alert:null},
      {name:'Verification Status', value:'Verified', valuePoints:100, weight:0.10,
       desc:'Whether markings were independently verified by a second operator.',
       sources:['Control Point marking file'],
       grades:[
        {l:'Verified',r:'Second-operator check',s:100, current:true},
        {l:'Unverified',r:'Single operator',    s:65}
       ],
       rec:'Marking has independent verification.', alert:null}
     ]},

    {name:'Image Alignment', score:88, workflowOK:true,
     desc:'How well images were aligned in feature matching and bundle adjustment.',
     weightInSub:0.35,
     ruleNotes:[],
     indicators:[
      {name:'Tie Point Count', value:'2.4 million', valuePoints:100, weight:0.25,
       desc:'Total tie points used in bundle adjustment.',
       sources:['Reconstruction report'],
       grades:[
        {l:'High',    r:'1 million or more',s:100, current:true},
        {l:'Adequate',r:'500 K or more',    s:80},
        {l:'Sparse',  r:'100 K or more',    s:50},
        {l:'Thin',    r:'less than 100 K',  s:20}
       ],
       rec:'Tie point volume is healthy.', alert:null},
      {name:'Reprojection Error', value:'0.6 px', valuePoints:78, weight:0.30,
       desc:'Mean pixel error of tie points after bundle adjustment.',
       sources:['Reconstruction report'],
       grades:[
        {l:'Tight',    r:'below 0.5 px',s:100},
        {l:'Acceptable',r:'below 0.8 px',s:78, current:true},
        {l:'Loose',    r:'below 1.2 px',s:50},
        {l:'Poor',     r:'1.2 px or more',s:20}
       ],
       rec:'Reprojection in the Acceptable band; investigate Bench 3 outliers for tighter solution.',
       alert:'Bench 3 area shows elevated reprojection (over 0.5 px) in clustered ties.'},
      {name:'Image Alignment Rate', value:'99.4%', valuePoints:100, weight:0.20,
       desc:'Share of images successfully aligned to the reconstruction.',
       sources:['Reconstruction report'],
       grades:[
        {l:'Complete',  r:'99% or more',s:100, current:true},
        {l:'Near-complete',r:'95% or more',s:80},
        {l:'Partial',   r:'85% or more',s:50},
        {l:'Failed',    r:'less than 85%',s:15, flag:'Alignment failure'}
       ],
       rec:'Nearly every image aligned.', alert:null},
      {name:'Bad Tie Clusters', value:'3', valuePoints:55, weight:0.15,
       desc:'Localised clusters of tie points with elevated reprojection.',
       sources:['Reconstruction report'],
       grades:[
        {l:'Clean',     r:'0',            s:100},
        {l:'Minor',     r:'1 to 2',       s:75},
        {l:'Significant',r:'3 to 5',      s:55, current:true},
        {l:'Severe',    r:'6 or more',    s:15}
       ],
       rec:'Re-tie the Bench 3 region for engineering-grade outputs.',
       alert:'3 bad clusters in Bench 3 area.'},
      {name:'Camera Self-Calibration', value:'Stable', valuePoints:90, weight:0.10,
       desc:'Stability of the self-calibrated camera intrinsics.',
       sources:['Reconstruction report'],
       grades:[
        {l:'Tight',  r:'Sigma below 0.05',s:100},
        {l:'Stable', r:'Sigma below 0.1', s:90, current:true},
        {l:'Loose',  r:'Sigma below 0.2', s:55},
        {l:'Unstable',r:'Sigma 0.2 or more',s:20}
       ],
       rec:'Self-calibration stable.', alert:null}
     ]},

    {name:'Radiometric Balancing', score:89, workflowOK:true,
     desc:'How well image colour and brightness were balanced across the mosaic.',
     weightInSub:0.15,
     ruleNotes:[],
     indicators:[
      {name:'Colour Balance', value:'Even', valuePoints:89, weight:1.0,
       desc:'Visual evenness of the orthomosaic colour across the AOI.',
       sources:['Orthophoto','ODM report'],
       grades:[
        {l:'Even',      r:'No visible banding',s:100},
        {l:'Acceptable',r:'Minor banding',     s:89, current:true},
        {l:'Banded',    r:'Visible seams',     s:55},
        {l:'Severe',    r:'Strong colour shifts',s:20, flag:'Radiometric inconsistency'}
       ],
       rec:'Minor banding is acceptable.', alert:null}
     ]},

    {name:'Image Quality (Reconstruction)', score:91, workflowOK:true,
     desc:'Quality of the imagery as seen by the reconstruction engine.',
     weightInSub:0.10,
     ruleNotes:[],
     indicators:[
      {name:'Mean Sharpness', value:'High', valuePoints:90, weight:0.30,
       desc:'Average image sharpness in the reconstruction set.',
       sources:['Image quality log'],
       grades:[
        {l:'High',  r:'Tier 1',s:100},
        {l:'Acceptable',r:'Tier 2',s:90, current:true},
        {l:'Soft',  r:'Tier 3',s:55},
        {l:'Blurry',r:'Tier 4',s:20, flag:'Blurred imagery'}
       ],
       rec:'Sharpness is acceptable.', alert:null},
      {name:'Noise Level', value:'Low', valuePoints:100, weight:0.20,
       desc:'Sensor noise level across the image set.',
       sources:['Image quality log'],
       grades:[
        {l:'Low',   r:'Clean',     s:100, current:true},
        {l:'Mild',  r:'Slight',    s:80},
        {l:'High',  r:'Notable',   s:45}
       ],
       rec:'Noise is low.', alert:null},
      {name:'Exposure Range', value:'Balanced', valuePoints:90, weight:0.20,
       desc:'How well the image set covered the dynamic range of the scene.',
       sources:['Image quality log'],
       grades:[
        {l:'Balanced',r:'Even histogram',  s:100},
        {l:'Acceptable',r:'Mild bias',     s:90, current:true},
        {l:'Skewed', r:'Notable bias',     s:55}
       ],
       rec:'Exposure spread is acceptable.', alert:null},
      {name:'Lens Distortion Resolved', value:'Yes', valuePoints:100, weight:0.15,
       desc:'Whether the reconstruction successfully modelled lens distortion.',
       sources:['Reconstruction report'],
       grades:[
        {l:'Resolved', r:'Modelled cleanly',s:100, current:true},
        {l:'Residual', r:'Minor residuals', s:75},
        {l:'Unresolved',r:'Residuals visible',s:30}
       ],
       rec:'Distortion modelled cleanly.', alert:null},
      {name:'Coverage Overlap (Effective)', value:'Strong', valuePoints:90, weight:0.15,
       desc:'Effective overlap seen by the reconstruction engine (post-alignment).',
       sources:['Reconstruction report'],
       grades:[
        {l:'Strong',    r:'High effective overlap',s:100},
        {l:'Acceptable',r:'Adequate',     s:90, current:true},
        {l:'Marginal',  r:'Borderline',   s:55}
       ],
       rec:'Effective overlap supports the reconstruction.', alert:null}
     ]},

    {name:'Calibration Confidence', score:95, workflowOK:true,
     desc:'Confidence in the camera calibration used in reconstruction.',
     weightInSub:0.10,
     ruleNotes:[],
     indicators:[
      {name:'Calibration Match', value:'Match', valuePoints:95, weight:1.0,
       desc:'Whether reconstruction used the correct calibration for the field camera.',
       sources:['Camera calibration file','Reconstruction report'],
       grades:[
        {l:'Match', r:'Make and model match',s:100},
        {l:'Partial',r:'Make only',          s:60, current:false},
        {l:'No match',r:'Neither',           s:20}
       ],
       rec:'Calibration matches the field camera.', alert:null}
     ]}
  ]
};

// =============================================================
var SUB_PROC_PRODUCTS={
  id:'sub-products', name:'Products', short:'Products',
  desc:'Point cloud, ground classification, DSM/DTM surfaces, orthophoto, and 3D mesh outputs.',
  score:92, workflowOK:true,
  blocks:[
    {name:'Point Cloud', score:91, workflowOK:true,
     desc:'Quality of the dense point cloud reconstruction.',
     weightInSub:0.25,
     ruleNotes:[],
     indicators:[
      {name:'Total Points', value:'148 million', valuePoints:100, weight:0.30,
       desc:'Total points in the dense cloud.',
       sources:['Point cloud file','Point cloud stats'],
       grades:[
        {l:'Dense',   r:'100 M or more', s:100, current:true},
        {l:'Adequate',r:'50 M or more',  s:80},
        {l:'Sparse',  r:'20 M or more',  s:55},
        {l:'Thin',    r:'less than 20 M',s:25}
       ],
       rec:'Point cloud is dense enough for engineering work.', alert:null},
      {name:'Point Density', value:'42 per square metre', valuePoints:100, weight:0.30,
       desc:'Average point density across the AOI.',
       sources:['Point cloud stats'],
       grades:[
        {l:'High',   r:'40 or more',s:100, current:true},
        {l:'Survey', r:'25 or more',s:85},
        {l:'Mapping',r:'10 or more',s:60},
        {l:'Low',    r:'less than 10',s:25}
       ],
       rec:'Density supports volumetric analytics.', alert:null},
      {name:'Voids', value:'None', valuePoints:100, weight:0.25,
       desc:'Detected void or low-density regions.',
       sources:['Point cloud stats'],
       grades:[
        {l:'Clean',     r:'0',         s:100, current:true},
        {l:'Minor',     r:'1 to 2',    s:75},
        {l:'Significant',r:'3 to 5',   s:45},
        {l:'Severe',    r:'6 or more', s:15, flag:'Cloud voids'}
       ],
       rec:'No voids detected.', alert:null},
      {name:'Noise Level', value:'Low', valuePoints:88, weight:0.15,
       desc:'Stray points outside the surface envelope.',
       sources:['Point cloud stats'],
       grades:[
        {l:'Low',      r:'Less than 1%',s:100},
        {l:'Acceptable',r:'Less than 3%',s:88, current:true},
        {l:'High',     r:'Less than 6%',s:50},
        {l:'Severe',   r:'6% or more',  s:20}
       ],
       rec:'Noise level acceptable.', alert:null}
     ]},

    {name:'Ground Classification', score:84, workflowOK:true,
     desc:'How well points were separated into ground vs. non-ground for the DTM.',
     weightInSub:0.15,
     ruleNotes:[],
     indicators:[
      {name:'Classification Coverage', value:'88%', valuePoints:88, weight:0.40,
       desc:'Share of the AOI assigned a definitive ground class.',
       sources:['Point cloud file'],
       grades:[
        {l:'Complete',r:'95% or more',s:100},
        {l:'Strong',  r:'85% or more',s:88, current:true},
        {l:'Partial', r:'70% or more',s:55},
        {l:'Sparse',  r:'less than 70%',s:25}
       ],
       rec:'Classification covers the great majority of the AOI.', alert:null},
      {name:'Bench 3 Confidence', value:'71%', valuePoints:75, weight:0.35,
       desc:'AI classification confidence in the flagged Bench 3 region.',
       sources:['Point cloud file','Analytics export'],
       grades:[
        {l:'Confident', r:'85% or more',s:100},
        {l:'Probable',  r:'70% or more',s:75, current:true},
        {l:'Uncertain', r:'50% or more',s:45},
        {l:'Unreliable',r:'less than 50%',s:15, flag:'Low classification confidence'}
       ],
       rec:'Bench 3 classification is provisional. Tie back to the reconstruction re-tie task.',
       alert:'Bench 3 classification confidence below 80% threshold.'},
      {name:'Vegetation Filter Quality', value:'Acceptable', valuePoints:85, weight:0.25,
       desc:'How well vegetation was filtered from ground.',
       sources:['Point cloud file'],
       grades:[
        {l:'Clean',     r:'No canopy residue',s:100},
        {l:'Acceptable',r:'Minor residue',    s:85, current:true},
        {l:'Residual',  r:'Notable residue',  s:50, flag:'Canopy residue'}
       ],
       rec:'Minor residue on Bench 3; consider manual cleanup before engineering use.', alert:null}
     ]},

    {name:'Elevation Surfaces', score:93, workflowOK:true,
     desc:'DSM and DTM raster quality.',
     weightInSub:0.20,
     ruleNotes:['Resolves to FINAL after the ODM report independent check.'],
     indicators:[
      {name:'Vertical Accuracy', value:'+/- 2.1 cm', valuePoints:100, weight:0.50,
       desc:'Vertical RMSE against check points.',
       sources:['ODM report','Check points'],
       grades:[
        {l:'Professional',r:'3 cm or less',s:100, current:true},
        {l:'Engineering', r:'5 cm or less',s:85},
        {l:'Mapping',     r:'10 cm or less',s:60},
        {l:'Reject',      r:'over 10 cm',  s:20}
       ],
       rec:'Vertical accuracy meets professional grade.', alert:null},
      {name:'Grid Resolution', value:'10 cm', valuePoints:90, weight:0.30,
       desc:'Cell size of the elevation raster.',
       sources:['DSM raster metadata','DTM raster metadata'],
       grades:[
        {l:'High',  r:'5 cm or less',s:100},
        {l:'Survey',r:'10 cm or less',s:90, current:true},
        {l:'Mapping',r:'25 cm or less',s:60},
        {l:'Coarse',r:'over 25 cm',  s:30}
       ],
       rec:'Resolution supports survey-grade work.', alert:null},
      {name:'Surface Completeness', value:'100%', valuePoints:100, weight:0.20,
       desc:'Share of the AOI covered by valid raster pixels.',
       sources:['DSM raster metadata','DTM raster metadata'],
       grades:[
        {l:'Complete', r:'100%',     s:100, current:true},
        {l:'Near-complete',r:'99% or more',s:85},
        {l:'Partial',  r:'95% or more',s:55},
        {l:'Gaps',     r:'less than 95%',s:20, flag:'Raster gaps'}
       ],
       rec:'No raster gaps.', alert:null}
     ]},

    {name:'Orthophoto', score:93, workflowOK:true,
     desc:'Orthomosaic image quality.',
     weightInSub:0.15,
     ruleNotes:[],
     indicators:[
      {name:'Resolution', value:'4.8 cm/pixel', valuePoints:90, weight:0.50,
       desc:'Ground sample distance of the orthomosaic.',
       sources:['Orthophoto raster metadata'],
       grades:[
        {l:'High',  r:'3 cm or less',s:100},
        {l:'Survey',r:'5 cm or less',s:90, current:true},
        {l:'Mapping',r:'10 cm or less',s:60},
        {l:'Coarse',r:'over 10 cm',  s:30}
       ],
       rec:'GSD meets the survey band.', alert:null},
      {name:'Visual Quality', value:'Clean', valuePoints:95, weight:0.50,
       desc:'Visible artefacts (seams, ghosting, motion blur) in the mosaic.',
       sources:['Orthophoto','ODM report'],
       grades:[
        {l:'Clean',     r:'No visible artefacts',s:100},
        {l:'Acceptable',r:'Minor artefacts',     s:95, current:true},
        {l:'Visible',   r:'Notable artefacts',   s:55},
        {l:'Severe',    r:'Dominant artefacts',  s:20, flag:'Mosaic artefacts'}
       ],
       rec:'Minor artefacts only; cleared for client delivery.', alert:null}
     ]},

    {name:'Split Workflow Quality', score:92, workflowOK:true,
     desc:'When the AOI was processed in tiles, how cleanly the tiles joined.',
     weightInSub:0.10,
     ruleNotes:[],
     indicators:[
      {name:'Seamline Quality', value:'ECC clean', valuePoints:100, weight:0.40,
       desc:'Algorithm used and seamline cleanliness.',
       sources:['ODM products report'],
       grades:[
        {l:'ECC clean',r:'Energy-cut, clean',s:100, current:true},
        {l:'ECC mixed',r:'Energy-cut, masked',s:85},
        {l:'Voronoi', r:'Geometric',         s:60},
        {l:'Naive',   r:'Centre-priority',   s:30}
       ],
       rec:'ECC seamlines produced minimal visible joins.', alert:null},
      {name:'Edge Match (Elevation)', value:'+/- 1.5 cm', valuePoints:90, weight:0.40,
       desc:'Average elevation discrepancy at tile boundaries.',
       sources:['ODM products report'],
       grades:[
        {l:'Tight',    r:'2 cm or less',s:100},
        {l:'Acceptable',r:'5 cm or less',s:90, current:true},
        {l:'Loose',    r:'10 cm or less',s:55},
        {l:'Mismatched',r:'over 10 cm',  s:20}
       ],
       rec:'Edges match cleanly.', alert:null},
      {name:'Tile Count', value:'4 tiles', valuePoints:80, weight:0.20,
       desc:'Number of tiles the AOI was split into.',
       sources:['ODM products report'],
       grades:[
        {l:'Single',  r:'Unsplit',  s:100},
        {l:'Few',     r:'2 to 6',   s:80, current:true},
        {l:'Many',    r:'7 or more',s:55}
       ],
       rec:'Tile count is reasonable for AOI size.', alert:null}
     ]},

    {name:'3D Model', score:82, workflowOK:true,
     desc:'Textured 3D mesh quality.',
     weightInSub:0.15,
     ruleNotes:[],
     indicators:[
      {name:'Mesh Triangles', value:'42 million', valuePoints:90, weight:0.50,
       desc:'Triangle count of the final mesh.',
       sources:['3D Model file'],
       grades:[
        {l:'Detailed',r:'50 M or more',s:100},
        {l:'Adequate',r:'20 M or more',s:90, current:true},
        {l:'Sparse',  r:'5 M or more', s:55},
        {l:'Coarse',  r:'less than 5 M',s:25}
       ],
       rec:'Mesh detail supports visualisation.', alert:null},
      {name:'Texture Resolution', value:'8K', valuePoints:75, weight:0.50,
       desc:'Resolution of the applied texture atlas.',
       sources:['3D Model file'],
       grades:[
        {l:'16K', r:'16K or higher',s:100},
        {l:'8K',  r:'8K',           s:75, current:true},
        {l:'4K',  r:'4K',           s:55},
        {l:'Low', r:'less than 4K', s:25}
       ],
       rec:'8K texture suits the AOI scale.', alert:null}
     ]}
  ]
};

// =============================================================
var SUB_PROC_REPORT={
  id:'sub-report', name:'Accuracy Report', short:'Report',
  desc:'Independent check-point verification -- the outcome-based truth that validates everything upstream.',
  score:94, workflowOK:true,
  blocks:[
    {name:'Check Point Verification', score:94, workflowOK:true,
     desc:'Independent measurement of accuracy using check points held back from the reconstruction.',
     weightInSub:1.00,
     ruleNotes:['Resolves the elevation surfaces from PARTIAL to FINAL.'],
     indicators:[
      {name:'Vertical RMSE', value:'+/- 2.1 cm', valuePoints:100, weight:0.30,
       desc:'Root-mean-square vertical error against check points.',
       sources:['Check points','ODM report'],
       grades:[
        {l:'Professional',r:'3 cm or less',s:100, current:true},
        {l:'Engineering', r:'5 cm or less',s:85},
        {l:'Mapping',     r:'10 cm or less',s:60},
        {l:'Reject',      r:'over 10 cm',  s:20}
       ],
       rec:'Vertical accuracy meets professional grade.', alert:null},
      {name:'Horizontal RMSE', value:'+/- 1.8 cm', valuePoints:100, weight:0.25,
       desc:'Root-mean-square horizontal error against check points.',
       sources:['Check points','ODM report'],
       grades:[
        {l:'Professional',r:'3 cm or less',s:100, current:true},
        {l:'Engineering', r:'5 cm or less',s:85},
        {l:'Mapping',     r:'10 cm or less',s:60},
        {l:'Reject',      r:'over 10 cm',  s:20}
       ],
       rec:'Horizontal accuracy meets professional grade.', alert:null},
      {name:'Check Point Count', value:'6', valuePoints:90, weight:0.20,
       desc:'Number of independent check points used.',
       sources:['Check points'],
       grades:[
        {l:'Robust',  r:'6 or more', s:100, current:true},
        {l:'Adequate',r:'4 to 5',    s:80},
        {l:'Sparse',  r:'2 to 3',    s:55},
        {l:'None',    r:'1 or fewer',s:20, flag:'Inadequate check points'}
       ],
       rec:'Six check points support strong statistical validation.', alert:null},
      {name:'Distribution', value:'Even', valuePoints:90, weight:0.15,
       desc:'How evenly the check points span the AOI.',
       sources:['Check points'],
       grades:[
        {l:'Even',    r:'All quadrants',s:100},
        {l:'Acceptable',r:'Most quadrants',s:90, current:true},
        {l:'Skewed',  r:'One quadrant under',s:55},
        {l:'Clustered',r:'Most in one area',s:25}
       ],
       rec:'Distribution supports robust validation.', alert:null},
      {name:'Outlier Count', value:'0', valuePoints:100, weight:0.10,
       desc:'Number of check points exceeding 3-sigma residuals.',
       sources:['Check points','ODM report'],
       grades:[
        {l:'Clean',  r:'0',  s:100, current:true},
        {l:'Minor',  r:'1',  s:75},
        {l:'Notable',r:'2 to 3',s:40},
        {l:'Severe', r:'4 or more',s:15, flag:'Multiple check point outliers'}
       ],
       rec:'No outliers detected.', alert:null}
     ]}
  ]
};

// =============================================================
// UNIVERSE 3 -- ANALYTICS
// 2 subsystems + 1 aggregator. Weight in OJS: 45%.
// =============================================================
var SUB_ANA_VOL={
  id:'sub-vol', name:'Volume Analytics', short:'Volume',
  desc:'Stockpile, pit, waste dump, and cut/fill volume calculations.',
  score:90, workflowOK:true,
  blocks:[
    {name:'Stockpile Confidence', score:92, workflowOK:true,
     desc:'Per-polygon confidence in stockpile volume.',
     weightInSub:0.30,
     ruleNotes:[],
     indicators:[
      {name:'Detection Recall', value:'100%', valuePoints:100, weight:0.30,
       desc:'Share of known stockpiles correctly detected by the AI.',
       sources:['Volume report (Stockpile)'],
       grades:[
        {l:'Excellent',r:'100%',     s:100, current:true},
        {l:'Strong',   r:'90% or more',s:85},
        {l:'Acceptable',r:'75% or more',s:60},
        {l:'Poor',     r:'less than 75%',s:25}
       ],
       rec:'All known stockpiles detected.', alert:null},
      {name:'AI Classification Confidence', value:'92%', valuePoints:100, weight:0.25,
       desc:'Average classifier confidence across detected stockpiles.',
       sources:['Volume report (Stockpile)'],
       grades:[
        {l:'High',    r:'90% or more',s:100, current:true},
        {l:'Adequate',r:'75% or more',s:80},
        {l:'Low',     r:'60% or more',s:50},
        {l:'Uncertain',r:'less than 60%',s:20}
       ],
       rec:'Classifier confidence is high.', alert:null},
      {name:'Volume Method', value:'TIN', valuePoints:100, weight:0.20,
       desc:'Method used to compute the volume.',
       sources:['Volume report (Stockpile)'],
       grades:[
        {l:'TIN',         r:'Triangulated base',s:100, current:true},
        {l:'Lowest-Elev', r:'Plane base',       s:80},
        {l:'Mean Plane',  r:'Average base',     s:60}
       ],
       rec:'TIN is the most accurate method.', alert:null},
      {name:'Reported Accuracy', value:'+/- 3%', valuePoints:100, weight:0.15,
       desc:'Stated uncertainty band on the volume.',
       sources:['Volume report (Stockpile)'],
       grades:[
        {l:'Engineering',r:'3% or less', s:100, current:true},
        {l:'Mapping',    r:'5% or less', s:80},
        {l:'Indicative', r:'10% or less',s:55},
        {l:'Reject',     r:'over 10%',   s:20}
       ],
       rec:'Volumes usable for reconciliation.', alert:null},
      {name:'False Positive Rate', value:'0%', valuePoints:100, weight:0.10,
       desc:'Spurious detections per total.',
       sources:['Volume report (Stockpile)'],
       grades:[
        {l:'Clean',  r:'0%',         s:100, current:true},
        {l:'Minor',  r:'less than 5%',s:80},
        {l:'Moderate',r:'less than 15%',s:50},
        {l:'Noisy',  r:'15% or more',s:20}
       ],
       rec:'No false positives.', alert:null}
     ]},

    {name:'Pit Confidence', score:88, workflowOK:true,
     desc:'Per-polygon confidence in pit volume.',
     weightInSub:0.25,
     ruleNotes:[],
     indicators:[
      {name:'Boundary Quality', value:'Clean', valuePoints:90, weight:0.30,
       desc:'How clean the pit boundary detection is.',
       sources:['Volume report (Pit)'],
       grades:[
        {l:'Clean',    r:'Tight boundary',s:100},
        {l:'Acceptable',r:'Minor noise',  s:90, current:true},
        {l:'Loose',    r:'Notable noise', s:55},
        {l:'Poor',     r:'Boundary unclear',s:20}
       ],
       rec:'Boundaries are clean.', alert:null},
      {name:'Depth Determination', value:'4.2 m mean', valuePoints:90, weight:0.30,
       desc:'How well the pit floor was reconstructed.',
       sources:['Volume report (Pit)'],
       grades:[
        {l:'Clean',    r:'No floor noise',s:100},
        {l:'Acceptable',r:'Mild noise',   s:90, current:true},
        {l:'Notable',  r:'Visible noise', s:55},
        {l:'Poor',     r:'Floor unreliable',s:20}
       ],
       rec:'Floor reconstruction is clean.', alert:null},
      {name:'Volume Method', value:'TIN', valuePoints:100, weight:0.20,
       desc:'Method used to compute the pit volume.',
       sources:['Volume report (Pit)'],
       grades:[
        {l:'TIN',         r:'Triangulated lid',s:100, current:true},
        {l:'Lowest-Elev', r:'Plane lid',       s:80}
       ],
       rec:'TIN method applied.', alert:null},
      {name:'Reported Accuracy', value:'+/- 4%', valuePoints:88, weight:0.20,
       desc:'Stated uncertainty band on the pit volume.',
       sources:['Volume report (Pit)'],
       grades:[
        {l:'Engineering',r:'3% or less',s:100},
        {l:'Mapping',    r:'5% or less',s:88, current:true},
        {l:'Indicative', r:'10% or less',s:55},
        {l:'Reject',     r:'over 10%',  s:20}
       ],
       rec:'Accuracy is in the Mapping band.', alert:null}
     ]},

    {name:'Waste Dump Confidence', score:78, workflowOK:true,
     desc:'Per-polygon confidence in waste dump volume.',
     weightInSub:0.20,
     ruleNotes:[],
     indicators:[
      {name:'Boundary Quality', value:'Provisional', valuePoints:55, weight:0.35,
       desc:'Cleanliness of the dump boundary detection.',
       sources:['Volume report (Waste Dump)'],
       grades:[
        {l:'Clean',     r:'Tight boundary',s:100},
        {l:'Acceptable',r:'Minor noise',   s:85},
        {l:'Provisional',r:'Notable noise',s:55, current:true, flag:'Boundary provisional'},
        {l:'Poor',      r:'Boundary unclear',s:20}
       ],
       rec:'Validate Dump 2 boundary before submission.',
       alert:'Dump 2 boundary depends on Bench 3 mesh quality.'},
      {name:'Volume Method', value:'TIN', valuePoints:100, weight:0.25,
       desc:'Method used.',
       sources:['Volume report (Waste Dump)'],
       grades:[
        {l:'TIN',        r:'Triangulated base',s:100, current:true},
        {l:'Lowest-Elev',r:'Plane base',       s:80}
       ],
       rec:'TIN method applied.', alert:null},
      {name:'Reported Accuracy', value:'+/- 6%', valuePoints:75, weight:0.25,
       desc:'Stated uncertainty band.',
       sources:['Volume report (Waste Dump)'],
       grades:[
        {l:'Engineering',r:'3% or less',s:100},
        {l:'Mapping',    r:'5% or less',s:88},
        {l:'Indicative', r:'10% or less',s:75, current:true},
        {l:'Reject',     r:'over 10%',  s:20}
       ],
       rec:'Indicative grade; flag in reporting.', alert:null},
      {name:'Toe-Crest Definition', value:'Acceptable', valuePoints:85, weight:0.15,
       desc:'How clearly toe and crest of the dump were resolved.',
       sources:['Volume report (Waste Dump)'],
       grades:[
        {l:'Sharp',     r:'Crisp toe and crest',s:100},
        {l:'Acceptable',r:'Mild blur',          s:85, current:true},
        {l:'Soft',      r:'Notable blur',       s:50}
       ],
       rec:'Toe-crest definition acceptable.', alert:null}
     ]},

    {name:'Cut-Fill Confidence', score:42, workflowOK:true,
     desc:'Confidence in cut/fill computation against a baseline survey.',
     weightInSub:0.25,
     ruleNotes:['Requires a baseline reference survey.'],
     indicators:[
      {name:'Baseline Recency', value:'29 days', valuePoints:100, weight:0.25,
       desc:'Age of the baseline survey used for comparison.',
       sources:['Volume report (Cut-Fill)','Reference survey'],
       grades:[
        {l:'Fresh',    r:'35 days or less', s:100, current:true},
        {l:'Acceptable',r:'90 days or less',s:80},
        {l:'Stale',    r:'180 days or less',s:55},
        {l:'Expired',  r:'over 180 days',  s:25}
       ],
       rec:'Baseline is current.', alert:null},
      {name:'Resolution Compatibility', value:'Matched', valuePoints:100, weight:0.20,
       desc:'Whether baseline and current surveys share comparable resolution.',
       sources:['Volume report (Cut-Fill)','Reference survey'],
       grades:[
        {l:'Matched',  r:'Same resolution',s:100, current:true},
        {l:'Close',    r:'Within 2x',     s:85},
        {l:'Mismatched',r:'Over 2x apart',s:40, flag:'Resolution mismatch'}
       ],
       rec:'Resolutions match.', alert:null},
      {name:'Bench 3 Sigma', value:'-3.8 sigma', valuePoints:25, weight:0.30,
       desc:'Statistical deviation between expected and observed change in the Bench 3 region.',
       sources:['Volume report (Cut-Fill)','Reference survey'],
       grades:[
        {l:'In-band',  r:'Within 2 sigma',  s:100},
        {l:'Suspect',  r:'Between 2 and 3 sigma',s:60},
        {l:'Critical', r:'Beyond 3 sigma',  s:25, current:true, flag:'Statistical anomaly'}
       ],
       rec:'Re-fly Bench 3 before certifying cut/fill.',
       alert:'Bench 3 at -3.8 sigma. Expected 312 cubic metres, observed 97.'},
      {name:'Net Volume', value:'-215 cubic metres', valuePoints:55, weight:0.15,
       desc:'Magnitude of the net cut/fill computed.',
       sources:['Volume report (Cut-Fill)'],
       grades:[
        {l:'Plausible',r:'Within expected range',s:100},
        {l:'Notable',  r:'Mild deviation',s:75},
        {l:'Suspect',  r:'Notable deviation',s:55, current:true},
        {l:'Implausible',r:'Strong deviation',s:20}
       ],
       rec:'Net volume sits below expected range; tied to the Bench 3 anomaly.', alert:null},
      {name:'Coverage Completeness', value:'100%', valuePoints:100, weight:0.10,
       desc:'Share of the AOI covered by valid cut/fill values.',
       sources:['Volume report (Cut-Fill)'],
       grades:[
        {l:'Complete',r:'100%',s:100, current:true},
        {l:'Partial', r:'95% or more',s:75},
        {l:'Sparse',  r:'less than 95%',s:35}
       ],
       rec:'Full coverage of the AOI.', alert:null}
     ]}
  ]
};

// =============================================================
var SUB_ANA_TERR={
  id:'sub-terr', name:'Terrain & Compare', short:'Terrain',
  desc:'Terrain derivative maps (slope, aspect, hillshade, contours) and surface comparison.',
  score:84, workflowOK:true,
  blocks:[
    {name:'Terrain Confidence', score:88, workflowOK:true,
     desc:'Quality of terrain derivative maps generated from the DSM/DTM.',
     weightInSub:0.50,
     ruleNotes:[],
     indicators:[
      {name:'Source Surface Quality', value:'High', valuePoints:93, weight:0.40,
       desc:'Quality of the underlying surface used to derive terrain maps.',
       sources:['DSM raster metadata','DTM raster metadata'],
       grades:[
        {l:'High',  r:'Vertical RMSE below 3 cm',s:100},
        {l:'Strong',r:'Vertical RMSE below 5 cm',s:93, current:true},
        {l:'Mapping',r:'Vertical RMSE below 10 cm',s:60},
        {l:'Poor',  r:'Vertical RMSE 10 cm or more',s:25}
       ],
       rec:'Source surface supports professional terrain products.', alert:null},
      {name:'Resolution Adequacy', value:'10 cm', valuePoints:88, weight:0.30,
       desc:'Whether the source resolution suits the requested terrain map type.',
       sources:['DSM raster metadata','DTM raster metadata'],
       grades:[
        {l:'High',      r:'5 cm or less', s:100},
        {l:'Survey',    r:'10 cm or less',s:88, current:true},
        {l:'Mapping',   r:'25 cm or less',s:60},
        {l:'Insufficient',r:'over 25 cm', s:25}
       ],
       rec:'Resolution is adequate for the requested derivatives.', alert:null},
      {name:'Coverage Completeness', value:'100%', valuePoints:100, weight:0.30,
       desc:'Share of the AOI covered by valid derivative values.',
       sources:['Terrain analysis output'],
       grades:[
        {l:'Complete',r:'100%',     s:100, current:true},
        {l:'Partial', r:'95% or more',s:75},
        {l:'Sparse',  r:'less than 95%',s:35}
       ],
       rec:'Full coverage of the AOI.', alert:null}
     ]},

    {name:'Compare Confidence', score:80, workflowOK:true,
     desc:'Confidence in quantitative or visual surface comparison results.',
     weightInSub:0.50,
     ruleNotes:[],
     indicators:[
      {name:'Surface A Quality', value:'High', valuePoints:93, weight:0.25,
       desc:'Quality of the first surface in the comparison.',
       sources:['Comparison surface data','DSM raster metadata'],
       grades:[
        {l:'High',  r:'Vertical RMSE below 3 cm',s:100},
        {l:'Strong',r:'Vertical RMSE below 5 cm',s:93, current:true},
        {l:'Mapping',r:'Vertical RMSE below 10 cm',s:60},
        {l:'Poor',  r:'Vertical RMSE 10 cm or more',s:25}
       ],
       rec:'Surface A is publication-grade.', alert:null},
      {name:'Surface B Quality', value:'Mapping', valuePoints:60, weight:0.25,
       desc:'Quality of the second surface in the comparison.',
       sources:['Comparison surface data','Reference survey'],
       grades:[
        {l:'High',  r:'Vertical RMSE below 3 cm',s:100},
        {l:'Strong',r:'Vertical RMSE below 5 cm',s:93},
        {l:'Mapping',r:'Vertical RMSE below 10 cm',s:60, current:true},
        {l:'Poor',  r:'Vertical RMSE 10 cm or more',s:25}
       ],
       rec:'Surface B sits at mapping grade; comparison precision is bounded by this.', alert:null},
      {name:'Temporal Alignment', value:'42 days apart', valuePoints:75, weight:0.20,
       desc:'How well the two surfaces represent comparable points in time.',
       sources:['Comparison surface data','Reference survey'],
       grades:[
        {l:'Aligned',  r:'Same week',     s:100},
        {l:'Acceptable',r:'Within 30 days',s:75, current:true},
        {l:'Loose',    r:'Within 90 days',s:50},
        {l:'Mismatched',r:'Over 90 days', s:25}
       ],
       rec:'Temporal alignment is acceptable.', alert:null},
      {name:'Resolution Compatibility', value:'Matched', valuePoints:100, weight:0.15,
       desc:'Whether surfaces share comparable resolution.',
       sources:['Comparison surface data'],
       grades:[
        {l:'Matched',  r:'Same resolution',s:100, current:true},
        {l:'Close',    r:'Within 2x',     s:80},
        {l:'Mismatched',r:'Over 2x apart',s:40}
       ],
       rec:'Resolutions match.', alert:null},
      {name:'Interpretability', value:'Quantitative', valuePoints:100, weight:0.15,
       desc:'Whether results support quantitative or only visual interpretation.',
       sources:['Comparison surface data'],
       grades:[
        {l:'Quantitative',r:'Numeric outputs valid',s:100, current:true},
        {l:'Visual',      r:'Visual only',           s:60}
       ],
       rec:'Results are quantitatively interpretable.', alert:null}
     ]}
  ]
};

// =============================================================
// FULL ONTOLOGY TREE
// Subsystems are flattened into direct universe -> block lists.
// Disabled (not-applicable-in-PPK) blocks are filtered out entirely.
// Where a BB name would be ambiguous without subsystem context, we prefix it lightly.
// =============================================================

// Subsystem-name prefixes applied to building blocks for clarity. Only used where
// the BB name alone could be confused with a block from another subsystem.
// Empty string means no prefix.
var BB_PREFIX_RULES = {
  // Drone -- names are already self-contained, no prefix needed
  'Image Capture':'', 'Mission Execution':'', 'Rover GNSS Quality':'',
  // Base -- distinguish RINEX/Session from rover/Control Point equivalents
  'RINEX Recording':'Base Station ', 'Session Quality':'',  // overridden contextually
  'Antenna Setup':'Base Station ', 'Base Position Quality':'',
  // Control Point
  'Device Recording':'Control Point ', 'Network Layout':'Control Point ', 'Coordinate Quality':'Control Point ',
  // Pre-Processing
  'PPK Solution Quality':'',
  // Reconstruction
  'Control Point Marking':'', 'Image Alignment':'', 'Radiometric Balancing':'',
  'Image Quality (Reconstruction)':'', 'Calibration Confidence':'',
  // Products
  'Point Cloud':'', 'Ground Classification':'', 'Elevation Surfaces':'',
  'Orthophoto':'', 'Split Workflow Quality':'', '3D Model':'',
  // Report
  'Check Point Verification':'',
  // Analytics
  'Stockpile Confidence':'', 'Pit Confidence':'', 'Waste Dump Confidence':'',
  'Cut-Fill Confidence':'', 'Terrain Confidence':'', 'Compare Confidence':''
};

// Build flat block list per universe, applying prefixes and filtering disabled.
function flattenSubsystems(subsystems){
  var blocks=[];
  subsystems.forEach(function(sub){
    sub.blocks.forEach(function(bb){
      if(bb.workflowOK===false) return; // hide disabled BBs entirely
      // Apply contextual prefix based on subsystem
      var name=bb.name;
      // Two "Session Quality" blocks (Base + Control Point) need disambiguation
      if(name==='Session Quality'){
        if(sub.short==='Base') name='Base Station Session Quality';
        else if(sub.short==='Control Point') name='Control Point Session Quality';
      } else if(BB_PREFIX_RULES.hasOwnProperty(name) && BB_PREFIX_RULES[name]){
        name = BB_PREFIX_RULES[name] + name;
      }
      // Clone the BB with the prefixed name; keep original ref for indicators
      blocks.push({
        name: name,
        originalName: bb.name,
        score: bb.score,
        desc: bb.desc,
        workflowOK: true,
        ruleNotes: bb.ruleNotes,
        indicators: bb.indicators
      });
    });
  });
  return blocks;
}


/* ── SUB_CAPTURE_CHECKPOINT — independent RTK check points (replaces preproc in Capture) ── */
var SUB_CAPTURE_CHECKPOINT={
  id:'sub-checkpoint', name:'Check Points', short:'CheckPt',
  desc:'Independent RTK check shots that validate the survey against external accuracy — not used in the adjustment.',
  score:58, workflowOK:true,
  blocks:[
    {name:'Check-Point Capture Completeness', score:58, workflowOK:true,
     desc:'Position sigma, fix type, correction age and log integrity at each check point.',
     weightInSub:0.45, ruleNotes:['A FLOAT/AUTONOMOUS fix zeros that point (per-point gate). All points FLOAT or zero-sigma fires the global gate.'],
     indicators:[
      {name:'Fix Type at Capture', value:'1 FLOAT', valuePoints:58, weight:0.30,
       desc:'Share of check points that achieved a FIXED RTK integer solution at the measurement epoch.',
       sources:['RTK rover log'],
       grades:[{l:'All Fixed',r:'every point FIXED',s:100},{l:'Mixed',r:'one point FLOAT',s:58,current:true},{l:'All Float',r:'global gate',s:0,flag:'CP_FLOAT'}],
       rec:'CP-002 captured FLOAT — re-occupy that point.', alert:null}
     ]}
  ,
    {name:'Check-Point Setup Confidence', score:62, workflowOK:true,
     desc:'Antenna height, pole stability, baseline, NTRIP, antenna type and device traceability.',
     weightInSub:0.35, ruleNotes:['Antenna-height-missing zeros that point (per-point gate).'],
     indicators:[
      {name:'Antenna Height Documented', value:'8/8', valuePoints:96, weight:0.40,
       desc:'Share of check points with a documented antenna height.',
       sources:['Field notes'],
       grades:[{l:'Documented',r:'measured + recorded',s:96,current:true},{l:'Missing',r:'per-point gate',s:0,flag:'CP_AH_MISSING'}],
       rec:'Antenna heights documented across the fleet.', alert:null}
     ]}
  ,
    {name:'Check-Point Observation Environment', score:62, workflowOK:true,
     desc:'PDOP, fix-hold duration, sky obstruction and ionospheric risk at the measurement epoch.',
     weightInSub:0.20, ruleNotes:[],
     indicators:[
      {name:'Position Sigma', value:'1 elevated', valuePoints:62, weight:0.45,
       desc:'RTK reported position uncertainty across the check points.',
       sources:['RTK rover log'],
       grades:[{l:'\u22645cm',r:'excellent',s:100},{l:'5-15cm',r:'CP-003 elevated',s:62,current:true},{l:'>15cm',r:'degraded',s:30}],
       rec:'CP-003 shows elevated sigma — verify against spec.', alert:null}
     ]}
  ]
};
var ONTOLOGY={
  workflow:'PPK',
  universes:[
    {id:'cap', name:'Capture',    desc:'Field data acquisition.',
     score:88, weight:0.20, col:'#4db896', glow:'rgba(77,184,150,',
     blocks: flattenSubsystems([SUB_CAPTURE_DRONE, SUB_CAPTURE_BASE, SUB_CAPTURE_GCP, SUB_CAPTURE_CHECKPOINT])},
    {id:'pro', name:'Processing', desc:'Photogrammetric reconstruction and product generation.',
     score:91, weight:0.35, col:'#5596cc', glow:'rgba(85,150,204,',
     blocks: flattenSubsystems([SUB_PROC_RECON, SUB_PROC_PRODUCTS, SUB_PROC_REPORT, SUB_CAPTURE_PREPROC])},
    {id:'ana', name:'Analytics',  desc:'Volumetrics, terrain derivatives, and surface comparison.',
     score:87, weight:0.45, col:'#00B4D8', glow:'rgba(0,180,216,',
     blocks: flattenSubsystems([SUB_ANA_VOL, SUB_ANA_TERR])}
  ]
};

// Overall Job Score computed from universe weights
var OJS=Math.round(
  ONTOLOGY.universes[0].score * ONTOLOGY.universes[0].weight +
  ONTOLOGY.universes[1].score * ONTOLOGY.universes[1].weight +
  ONTOLOGY.universes[2].score * ONTOLOGY.universes[2].weight
);

// Build GATES from ONTOLOGY: rings are universes, planets are building blocks (flat list)
var GATES = ONTOLOGY.universes.map(function(u, ui){
  var clockAngle = ui===0 ? -Math.PI/2 : ui===1 ? Math.PI/2 : 0;
  var R = ui===0 ? 0.82 : ui===1 ? 0.56 : 0.30;
  var spd = ui===0 ? 0.0022 : ui===1 ? 0.0032 : 0.0055;
  var planets = u.blocks.map(function(bb){
    return {
      n: bb.name,
      sh: bb.name,
      v: bb.score,
      st: bb.score>=85 ? 'ok' : bb.score>=70 ? 'warn' : 'crit',
      workflowOK: true,
      block: bb
    };
  });
  return {
    name: u.name,
    score: u.score,
    col: u.col,
    glow: u.glow,
    R: R,
    spd: spd,
    bw: 0.082,
    clockAngle: clockAngle,
    universe: u,
    planets: planets
  };
});

var pSt=GATES.map(function(g){return g.planets.map(function(){return {t:0};});});

var PULSES=[];
function initPulses(){
  PULSES=[];
  for(var gi=0;gi<3;gi++)
    for(var k=0;k<3;k++)
      PULSES.push({gi:gi,angle:(k/3)*Math.PI*2,spd:gi===0?0.0028:gi===1?0.0042:0.0065,arc:Math.PI*0.17});
}

// UI semantic state colours — used by panels for hero scores, sibling-tab dots, etc.
// The orbital uses its own muted warm-tone variants inline in render().
function sCol(v){return v>=90?'#4db896':v>=80?'#5596cc':v>=70?'#c4882a':'#b84444';}

// Orbital planet colour — muted warm tones, per design-system orbital spec.
// Used only inside the canvas render path, not for panels.
function sColOrbital(v){
  if(v>=80) return 'rgba(226,238,246,.92)';   // steel-white marker (good)
  if(v>=70) return 'rgba(156,130,82,.82)';    // titanium bronze (warn)
  return 'rgba(145,82,80,.82)';                // oxide red, controlled (crit)
}
function hexRGB(h){return parseInt(h.slice(1,3),16)+','+parseInt(h.slice(3,5),16)+','+parseInt(h.slice(5,7),16);}

// -- CURVED TEXT along arc --
function drawCurvedText(text, cx, cy, radius, startAngle, col, fontSize, alpha) {
  ctx.save();
  ctx.font = '600 ' + fontSize + 'px IBM Plex Mono';
  ctx.fillStyle = col;
  ctx.globalAlpha = alpha;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  var chars = text.split('');
  var totalW = 0;
  var widths = [];
  for (var i = 0; i < chars.length; i++) {
    var cw = ctx.measureText(chars[i]).width;
    widths.push(cw);
    totalW += cw;
  }
  var totalArc = totalW / radius;
  var curAngle = startAngle - totalArc / 2;

  for (var ci = 0; ci < chars.length; ci++) {
    var charAngle = curAngle + widths[ci] / 2 / radius;
    var tx = cx + Math.cos(charAngle) * radius;
    var ty = cy + Math.sin(charAngle) * radius;
    ctx.save();
    ctx.translate(tx, ty);
    ctx.rotate(charAngle + Math.PI / 2);
    ctx.fillText(chars[ci], 0, 0);
    ctx.restore();
    curAngle += widths[ci] / radius;
  }
  ctx.restore();
}

function getMaxR(cvW) {
  var topOff=100, botOff=115;
  return Math.min(cvW*0.88, H-topOff-botOff) * 0.44 * (1 + 0.16*zoomT);
}
function ringR(gi,maxR){
  var base=maxR*GATES[gi].R;
  if(zoomT>0&&activeGate>=0){
    if(gi===activeGate) return base*(1+0.14*zoomT);
    return base*(1-0.07*zoomT);
  }
  return base;
}
function bandW(gi,maxR){return maxR*GATES[gi].bw;}
function pRad(gi,maxR){
  // Min radius is the overview "dot" size; max is the expanded zoomed-in body size.
  // Sized so that planets are visible but not overwhelming at the new BB count (12-14 per ring).
  var mn=maxR*0.009, mx=maxR*0.040;
  if(activeGate===gi) return mn+(mx-mn)*zoomT;
  // When another ring is active, this ring's planets shrink further
  return mn*(activeGate>=0?0.5:1);
}
function pAngle(gi,pi){
  // Planets evenly distributed around the full ring; rotate slowly with lastRot.
  var g=GATES[gi];
  var n=g.planets.length;
  if(n===0) return 0;
  return (lastRot[gi]||0) - Math.PI/2 + (pi/n)*Math.PI*2;
}

function resize(){
  W=window.innerWidth; H=window.innerHeight;
  CV.width=Math.round(W*DPR); CV.height=Math.round(H*DPR);
  CV.style.width=W+'px'; CV.style.height=H+'px';
  ctx.setTransform(DPR,0,0,DPR,0,0);
}
window.addEventListener('resize',resize);

// -- BUILD SCORE LABEL HTML ELEMENTS --
function buildScoreLabels(){
  var cont=document.getElementById('score-labels');
  cont.innerHTML='';
  GATES.forEach(function(g,gi){
    var el=document.createElement('div');
    el.className='slbl'; el.id='slbl-'+gi;
    var fs=Math.max(18,Math.min(32,Math.round(lastMaxR*0.078)));
    el.innerHTML='<div class="sl-inner">'
      +'<div class="sl-num" style="font-size:'+fs+'px;color:rgba(230,240,248,.92);">'+g.score+'<span class="sl-pct">%</span></div>'
      +'<div class="sl-name">'+g.name+'</div>'
      +'</div>';
    el.onclick=(function(i){return function(){zoomIntoGate(i);};})(gi);
    cont.appendChild(el);
    setTimeout(function(e){e.classList.add('show');},500+gi*140,el);
  });
}

// -- RENDER --
function render(){
  if(W<10||H<10){requestAnimationFrame(render);return;}
  zoomT+=(zoomTarget-zoomT)*0.08; if(Math.abs(zoomTarget-zoomT)<0.001) zoomT=zoomTarget;
  slideT+=(slideTarget-slideT)*0.07; if(Math.abs(slideTarget-slideT)<0.001) slideT=slideTarget;

  var panelFrac=0.44;
  var cvW=Math.round((1-panelFrac*slideT)*W);
  if(Math.abs(CV.clientWidth-cvW)>1){
    CV.width=cvW*DPR; CV.height=H*DPR;
    CV.style.width=cvW+'px'; CV.style.height=H+'px';
    ctx.setTransform(DPR,0,0,DPR,0,0);
  }

  var topOff=100, botOff=115;
  var maxR=getMaxR(cvW);
  lastMaxR=maxR;
  CX=cvW/2;
  CY=topOff+(H-topOff-botOff)/2;

  ctx.clearRect(0,0,cvW,H);

  // BACKGROUND — near-black base, with subtle radial centre-lift and edge depth
  ctx.fillStyle='#020308';
  ctx.fillRect(0,0,cvW,H);

  // Centre-lift gradient — lifts the centre subtly without colour cast
  var lift=ctx.createRadialGradient(CX,CY,0,CX,CY,Math.max(cvW,H)*0.58);
  lift.addColorStop(0,'rgba(255,255,255,.055)');
  lift.addColorStop(0.34,'rgba(95,110,120,.035)');
  lift.addColorStop(1,'rgba(0,0,0,0)');
  ctx.fillStyle=lift; ctx.fillRect(0,0,cvW,H);

  // Edge depth — darker corners
  var depth=ctx.createRadialGradient(CX,CY,0,CX,CY,Math.max(cvW,H)*0.84);
  depth.addColorStop(0,'rgba(0,0,0,0)');
  depth.addColorStop(1,'rgba(0,0,0,.62)');
  ctx.fillStyle=depth; ctx.fillRect(0,0,cvW,H);

  // Stars — barely there
  for(var s=0;s<36;s++){
    ctx.beginPath();
    ctx.arc(Math.abs(Math.sin(s*31.7)*cvW),Math.abs(Math.cos(s*17.3)*H),0.4,0,Math.PI*2);
    ctx.fillStyle='rgba(238,238,234,'+(0.028+0.018*Math.sin(s+frame*0.002))+')';
    ctx.fill();
  }

  // -- THICK BANDS --
  GATES.forEach(function(g,gi){
    var R=ringR(gi,maxR);
    var bw=bandW(gi,maxR);
    var Ro=R+bw/2, Ri=R-bw/2;
    // Idle modulation: rotation speed gently breathes (per design-system motion spec)
    var idle=0.82+0.18*Math.sin(frame*0.006);
    var rot=frame*g.spd*idle;
    lastRot[gi]=rot;
    var isA=activeGate===gi;
    var hasActive=activeGate>=0;
    // Inactive rings recede heavily when one is active (10% opacity)
    var dim=hasActive&&!isA?0.10:1.0;
    var sa=rot-Math.PI/2, ea=sa+(g.score/100)*Math.PI*2;

    // Selected-ring halo — soft cyan glow, only on active
    if(isA){
      ctx.beginPath(); ctx.arc(CX,CY,R,0,Math.PI*2);
      ctx.strokeStyle='rgba(148,212,232,.18)';
      ctx.lineWidth=bw*1.26;
      ctx.globalAlpha=.18; ctx.stroke(); ctx.globalAlpha=1;
    }

    // Track fill — almost nothing, 1% white wash
    ctx.beginPath();
    ctx.arc(CX,CY,Ro,0,Math.PI*2,false);
    ctx.arc(CX,CY,Ri,0,Math.PI*2,true);
    ctx.fillStyle='rgba(255,255,255,.010)';
    ctx.globalAlpha=dim; ctx.fill(); ctx.globalAlpha=1;

    // Track edges — 0.5px hairlines, slightly brighter outer
    ctx.beginPath(); ctx.arc(CX,CY,Ro,0,Math.PI*2);
    ctx.strokeStyle=isA?'rgba(156,207,224,.085)':'rgba(238,238,234,.045)';
    ctx.lineWidth=0.5; ctx.globalAlpha=dim; ctx.stroke(); ctx.globalAlpha=1;
    ctx.beginPath(); ctx.arc(CX,CY,Ri,0,Math.PI*2);
    ctx.strokeStyle=isA?'rgba(156,207,224,.085)':'rgba(238,238,234,.024)';
    ctx.lineWidth=0.5; ctx.globalAlpha=dim; ctx.stroke(); ctx.globalAlpha=1;

    // Score arc fill wash — subtle, monochrome at rest, accent-low when active
    ctx.beginPath();
    ctx.arc(CX,CY,Ro,sa,ea,false);
    ctx.arc(CX,CY,Ri,ea,sa,true);
    ctx.closePath();
    ctx.fillStyle=isA?'rgba(156,207,224,.045)':'rgba(238,238,234,.018)';
    ctx.globalAlpha=dim; ctx.fill(); ctx.globalAlpha=1;

    // Inner arc — primary signal line
    ctx.beginPath(); ctx.arc(CX,CY,Ri,sa,ea);
    ctx.strokeStyle=isA?'rgba(156,207,224,.66)':'rgba(238,238,234,.46)';
    ctx.lineWidth=isA?1.35:.8;
    ctx.globalAlpha=dim; ctx.stroke(); ctx.globalAlpha=1;

    // Outer arc echo — 0.5px hairline at low opacity
    ctx.beginPath(); ctx.arc(CX,CY,Ro,sa,ea);
    ctx.strokeStyle=isA?'rgba(156,207,224,.16)':'rgba(238,238,234,.09)';
    ctx.lineWidth=0.5; ctx.globalAlpha=dim; ctx.stroke(); ctx.globalAlpha=1;

    // Arc head + bright core
    var hx=CX+Math.cos(ea)*R, hy=CY+Math.sin(ea)*R;
    ctx.beginPath(); ctx.arc(hx,hy,isA?2.2:1.6,0,Math.PI*2);
    ctx.fillStyle=isA?'rgba(214,236,242,.86)':'rgba(238,238,234,.70)';
    ctx.globalAlpha=dim; ctx.fill(); ctx.globalAlpha=1;
    ctx.beginPath(); ctx.arc(hx,hy,.8,0,Math.PI*2);
    ctx.fillStyle='rgba(255,255,255,.9)';
    ctx.globalAlpha=dim; ctx.fill(); ctx.globalAlpha=1;

    // Sparse tick marks — 12 quiet marks, very low opacity
    for(var t=0;t<12;t++){
      var ta=rot-Math.PI/2+(t/12)*Math.PI*2;
      ctx.beginPath();
      ctx.moveTo(CX+Math.cos(ta)*(Ri+1.6),CY+Math.sin(ta)*(Ri+1.6));
      ctx.lineTo(CX+Math.cos(ta)*(Ro-1.6),CY+Math.sin(ta)*(Ro-1.6));
      ctx.strokeStyle='rgba(238,238,234,.105)';
      ctx.lineWidth=0.55;
      ctx.globalAlpha=dim; ctx.stroke(); ctx.globalAlpha=1;
    }

    // -- CURVED GATE NAME inside band --
    var nameRadius = Ri + bw * 0.38;
    var nameFontSz = Math.max(8, Math.round(bw * 0.44));
    var nameStartAngle = -Math.PI/2;
    var nameAlpha = isA ? Math.max(0, 0.46 - 0.42*zoomT) : 0.42;
    if(nameAlpha > 0.04) {
      drawCurvedText(g.name.toUpperCase(), CX, CY, nameRadius, nameStartAngle,
        isA?'rgba(214,236,242,.42)':'rgba(238,238,234,.38)', nameFontSz, nameAlpha * dim);
    }

    // Pulses — disabled along with continuous motion. The accent pulses traveled
    // along the active ring; with frame frozen they would render as static streaks
    // at arbitrary angles, which reads as visual noise. Cleaner to omit them entirely.
    // The active ring is still distinguished by its halo, inner-arc brightness, and zoom.

    // -- PLANETS --
    var pr=pRad(gi,maxR);
    g.planets.forEach(function(pl,pi){
      var pAng=pAngle(gi,pi);
      var px=CX+Math.cos(pAng)*R, py=CY+Math.sin(pAng)*R;
      var pc = sColOrbital(pl.v);
      var ps=pSt[gi][pi];
      // Visibility tween: planets fade in only when ring is active (reference behaviour)
      var target = (isA && zoomT>0.2) ? 1 : 0;
      ps.t+=(target-ps.t)*0.07;
      if(ps.t<0.02) return;
      var pt=ps.t;
      var isAP=activePlanet===pi&&activeGate===gi;

      // Soft halo — gentler than before, no shadow blur
      var pg=ctx.createRadialGradient(px,py,0,px,py,pr*2.8);
      pg.addColorStop(0,isAP?'rgba(238,248,255,.14)':'rgba(238,248,255,.045)');
      pg.addColorStop(1,'rgba(230,238,244,0)');
      ctx.beginPath(); ctx.arc(px,py,pr*3.2,0,Math.PI*2);
      ctx.fillStyle=pg; ctx.globalAlpha=pt*dim; ctx.fill();

      // Body — muted warm tones (no shadow blur)
      ctx.beginPath(); ctx.arc(px,py,pr*0.78,0,Math.PI*2);
      ctx.fillStyle=pc;
      ctx.globalAlpha=pt*dim; ctx.fill();

      // Selection ring (faint)
      if(isAP){
        ctx.beginPath(); ctx.arc(px,py,pr*1.55,0,Math.PI*2);
        ctx.strokeStyle='rgba(220,230,238,.12)'; ctx.lineWidth=0.6;
        ctx.globalAlpha=pt*0.55; ctx.stroke();
      }

      // Bright core
      ctx.beginPath(); ctx.arc(px,py,Math.max(1.4,pr*0.20),0,Math.PI*2);
      ctx.fillStyle='rgba(255,255,255,.96)';
      ctx.globalAlpha=pt*0.90*dim; ctx.fill();
      ctx.globalAlpha=1;

      // Floating telemetry label — no card, no border, just text with shadow
      if(pt>0.45&&isA){
        var lfsz=Math.max(15,Math.round(maxR*0.029));
        var nfsz=Math.max(12,Math.round(maxR*0.021));
        var labelOffset=pr*6.8 + maxR*0.032;
        var lx3=CX+Math.cos(pAng)*(R+labelOffset);
        var ly3=CY+Math.sin(pAng)*(R+labelOffset);
        lx3=Math.max(88,Math.min(lx3,cvW-88));
        ly3=Math.max(120,Math.min(ly3,H-120));
        ctx.save();
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.globalAlpha=pt*dim;
        ctx.shadowColor='rgba(0,0,0,.74)'; ctx.shadowBlur=12; ctx.shadowOffsetY=3;
        ctx.font='500 '+(lfsz+2)+'px Barlow';
        ctx.fillStyle='rgba(245,245,240,.90)';
        ctx.fillText(pl.v+'%',lx3,ly3-8);
        ctx.font='500 '+nfsz+'px Barlow';
        ctx.fillStyle='rgba(238,238,234,.46)';
        ctx.fillText(pl.sh,lx3,ly3+14);
        ctx.shadowBlur=0; ctx.shadowOffsetY=0;
        ctx.globalAlpha=1; ctx.restore();
      }
    });

    // -- SCORE LABEL (HTML element) -- update position --
    var el=document.getElementById('slbl-'+gi);
    if(el){
      var clockAng=g.clockAngle;
      var labelR=Ro+maxR*0.055;
      var lx2=CX+Math.cos(clockAng)*labelR;
      var ly2=CY+Math.sin(clockAng)*labelR;

      // Anchor based on clock position
      if(gi===0){ // 12-o'clock -- above
        el.style.left=Math.round(lx2)+'px';
        el.style.top=Math.round(ly2)+'px';
        el.style.transform='translate(-50%,-100%)';
      } else if(gi===1){ // 6-o'clock -- below
        el.style.left=Math.round(lx2)+'px';
        el.style.top=Math.round(ly2)+'px';
        el.style.transform='translate(-50%,0)';
      } else { // 3-o'clock -- right
        el.style.left=Math.round(lx2)+'px';
        el.style.top=Math.round(ly2)+'px';
        el.style.transform='translate(0,-50%)';
      }
      // Dim OTHER gates heavily; keep active gate score visible
      var scoreDim=hasActive&&!isA?0.10:1.0;
      el.style.opacity=String(scoreDim);
    }
  });

  // -- CENTRE --
  var iR=ringR(2,maxR)-bandW(2,maxR)/2;
  // Very subtle accent glow — single soft white-to-transparent radial, no cyan flood
  var cg=ctx.createRadialGradient(CX,CY,0,CX,CY,iR*1.2);
  cg.addColorStop(0,'rgba(255,255,255,.035)');
  cg.addColorStop(1,'rgba(148,212,232,0)');
  ctx.beginPath(); ctx.arc(CX,CY,iR*1.2,0,Math.PI*2); ctx.fillStyle=cg; ctx.fill();

  // Rotating tick fragments — monochrome, hinting at computation
  var r3=frame*0.004;
  for(var f=0;f<3;f++){
    var fa=r3+f*Math.PI*2/3;
    ctx.beginPath(); ctx.arc(CX,CY,iR*0.72,fa,fa+Math.PI*0.18);
    ctx.strokeStyle='rgba(238,238,234,.075)'; ctx.lineWidth=.75;
    ctx.stroke();
  }

  // Dark disc
  var disc=ctx.createRadialGradient(CX,CY,0,CX,CY,iR*0.88);
  disc.addColorStop(0,'rgba(2,3,8,.99)');
  disc.addColorStop(.72,'rgba(2,3,8,.96)');
  disc.addColorStop(1,'rgba(10,12,16,.78)');
  ctx.beginPath(); ctx.arc(CX,CY,iR*0.88,0,Math.PI*2); ctx.fillStyle=disc; ctx.fill();

  // Subtle computational texture inside centre disc
  ctx.save();
  ctx.beginPath(); ctx.arc(CX,CY,iR*0.82,0,Math.PI*2); ctx.clip();
  ctx.strokeStyle='rgba(238,238,234,.018)'; ctx.lineWidth=0.5;
  for(var gx=-iR; gx<=iR; gx+=iR*0.18){
    ctx.beginPath(); ctx.moveTo(CX+gx,CY-iR); ctx.lineTo(CX+gx,CY+iR); ctx.stroke();
  }
  for(var gy=-iR; gy<=iR; gy+=iR*0.18){
    ctx.beginPath(); ctx.moveTo(CX-iR,CY+gy); ctx.lineTo(CX+iR,CY+gy); ctx.stroke();
  }
  ctx.restore();

  // Hairline border on the disc
  ctx.beginPath(); ctx.arc(CX,CY,iR*0.88,0,Math.PI*2);
  ctx.strokeStyle='rgba(238,238,234,.055)'; ctx.lineWidth=0.6; ctx.stroke();

  // Sync master
  var mel=document.getElementById('master');
  if(mel){
    mel.style.left=Math.round(CX)+'px';
    mel.style.top=Math.round(CY)+'px';
    mel.style.transform='translate(-50%,-50%)';
  }
  document.getElementById('ms-num').style.fontSize=stage===0?'clamp(92px,11vw,150px)':'clamp(48px,5.3vw,68px)';
  // Grade label fades out when a ring is zoomed (ring's gate label takes over)
  var msGrade=document.getElementById('ms-grade');
  if(msGrade){
    msGrade.style.opacity=stage===0?'1':'0';
    msGrade.style.transition='opacity .4s';
  }

  // Sentence
  var outerBot=CY+ringR(0,maxR)+bandW(0,maxR)/2;
  var sentBottom=H-outerBot-50;
  var sent=document.getElementById('sentence');
  if(sent){
    sent.style.bottom=Math.max(58,Math.round(sentBottom))+'px';
    sent.style.opacity=stage>=1?'0':'1';
  }

  // Continuous motion disabled — frame stays at 0 so the rings, stars,
  // and centre disc ticks render at deterministic positions instead of animating.
  // The render loop still runs every frame to drive zoom/slide tweens which
  // are independent of frame and need to interpolate on click interactions.
  // frame++;
  requestAnimationFrame(render);
}

// -- HIT TESTING --
function getRing(mx,my){
  var cvW=(1-0.44*slideT)*W;
  var mxR=getMaxR(cvW), d=Math.sqrt((mx-CX)*(mx-CX)+(my-CY)*(my-CY));
  for(var gi=0;gi<3;gi++){
    var R=ringR(gi,mxR), bw=bandW(gi,mxR);
    if(d>=R-bw/2-4&&d<=R+bw/2+4) return gi;
  }
  return -1;
}
function getPlanet(mx,my){
  if(activeGate<0) return -1;
  var cvW=(1-0.44*slideT)*W;
  var mxR=getMaxR(cvW), R=ringR(activeGate,mxR), pr=pRad(activeGate,mxR);
  for(var pi=0;pi<GATES[activeGate].planets.length;pi++){
    var pa=pAngle(activeGate,pi);
    var px=CX+Math.cos(pa)*R, py=CY+Math.sin(pa)*R;
    if(Math.sqrt((mx-px)*(mx-px)+(my-py)*(my-py))<Math.max(pr+16,28)) return pi;
  }
  return -1;
}

var hint=document.getElementById('rhint');
CV.addEventListener('mousemove',function(e){
  var pl=stage>=1?getPlanet(e.clientX,e.clientY):-1;
  var ring=pl>=0?-1:getRing(e.clientX,e.clientY);
  if(pl>=0){
    CV.style.cursor='pointer';
    var p=GATES[activeGate].planets[pl];
    hint.textContent=p.n+' -- '+p.v+'%';
  } else if(ring>=0){
    CV.style.cursor='pointer';
    hint.textContent=stage===0?'Click to explore '+GATES[ring].name:(activeGate===ring?'Click to close':'Switch to '+GATES[ring].name);
  } else {CV.style.cursor='default'; hint.classList.remove('show'); return;}
  hint.style.left=(e.clientX+14)+'px'; hint.style.top=(e.clientY-34)+'px';
  hint.classList.add('show');
});
CV.addEventListener('mouseleave',function(){hint.classList.remove('show');});
CV.addEventListener('click',function(e){
  if(stage>=1){var pl=getPlanet(e.clientX,e.clientY);if(pl>=0){openPlanet(activeGate,pl);return;}}
  var ring=getRing(e.clientX,e.clientY);
  if(ring>=0){stage===1&&activeGate===ring?zoomOut():zoomIntoGate(ring);return;}
  if(stage===1) zoomOut(); else if(stage===2) closeDetail();
});

function zoomIntoGate(gi){activeGate=gi;activePlanet=-1;stage=1;zoomTarget=1;slideTarget=0;document.getElementById('bnav').style.opacity='0';}
function zoomOut(){activeGate=-1;activePlanet=-1;stage=0;zoomTarget=0;slideTarget=0;document.getElementById('detail-panel').classList.remove('open');document.getElementById('bnav').style.opacity='1';}

// Panel state shared by Confidence-Score and Deliverables panels
var panelMode=null;       // 'bb' | 'del' | null
var activeDel=null;       // {gi: group index, di: item index} when panelMode==='del'

function openPlanet(gi,pi){panelMode='bb';activePlanet=pi;stage=2;slideTarget=1;buildPanel(gi,pi);document.getElementById('detail-panel').classList.add('open');}
function closeDetail(){
  document.getElementById('detail-panel').classList.remove('open');
  document.body.classList.remove('panel-open');
  if(panelMode==='bb'){
    activePlanet=-1; stage=1; slideTarget=0;
  } else if(panelMode==='del'){
    activeDel=null;
    // Restore bnav since deliverables view normally shows it
    var bn=document.getElementById('bnav'); if(bn) bn.style.opacity='1';
  }
  panelMode=null;
}
function switchPlanet(gi,pi){activePlanet=pi;buildPanel(gi,pi);}

function openDeliverable(gi,di){
  panelMode='del';
  activeDel={gi:gi, di:di};
  buildDelPanel(gi,di);
  document.getElementById('detail-panel').classList.add('open');
  document.body.classList.add('panel-open');
  // Hide bottom navigation while panel is open to avoid overlay over panel content
  var bn=document.getElementById('bnav'); if(bn) bn.style.opacity='0';
}
function switchDeliverable(gi,di){
  activeDel={gi:gi, di:di};
  buildDelPanel(gi,di);
}

function buildPanel(gi,pi){
  var g=GATES[gi]; var pl=g.planets[pi];
  var bb=pl.block;
  var universe=g.universe;
  var col = sCol(pl.v);

  document.getElementById('dp-chip').textContent=universe.name;
  document.getElementById('dp-pname').textContent=bb.name;

  var inds=bb.indicators||[];

  // Classify each indicator into a triage bucket (review/passing) and compute
  // a tally for the hero sub-line. An indicator "needs review" if its derived
  // state is warn or crit, OR if it carries an alert. Passing indicators have
  // state==='good' and no alert.
  var classified = inds.map(function(ind){ return {ind:ind, info:classifyIndicator(ind)}; });
  var review  = classified.filter(function(c){ return c.info.needsReview; });
  var passing = classified.filter(function(c){ return !c.info.needsReview; });

  // Sort review indicators: crit before warn, then lowest score first within state
  review.sort(function(a,b){
    var stOrder = {crit:0, warn:1, good:2, neutral:3};
    var d = stOrder[a.info.state] - stOrder[b.info.state];
    if(d!==0) return d;
    var as = (a.info.score==null) ? 999 : a.info.score;
    var bs = (b.info.score==null) ? 999 : b.info.score;
    return as - bs;
  });

  // Hero sub-line: only shown when there's a mix of passing AND review.
  // Pure-passing or pure-review states make the line tautological — the section
  // header below carries that information already.
  var sublineEl = document.getElementById('dp-gsub');
  if(passing.length && review.length){
    sublineEl.textContent = inds.length + ' indicators \u00b7 '
                          + passing.length + ' passing \u00b7 '
                          + review.length + ' need'+(review.length===1?'s':'')+' review';
    sublineEl.style.display = '';
  } else {
    sublineEl.textContent = '';
    sublineEl.style.display = 'none';
  }

  var bs=document.getElementById('dp-bscore');
  bs.innerHTML=pl.v+'<span class="dp-bpct">%</span>';
  bs.style.color=col;

  // Hero status text dropped — the score colour carries the state, and the
  // section headers ("Needs Review" / "Indicators") reinforce it below.
  // Other panels (deliverable, hardware-BB) still write dp-status; clear it here
  // so a stale value from the previous panel doesn't bleed through.
  var statusEl = document.getElementById('dp-status');
  statusEl.textContent = '';
  statusEl.style.display = 'none';

  // Sibling-BB tabs across the whole universe
  var tabs='';
  g.planets.forEach(function(p2,i){
    var c2 = sCol(p2.v);
    tabs+='<div class="dp-tab'+(i===pi?' active':'')+'" onclick="switchPlanet('+gi+','+i+')">'
      +'<div class="dp-tab-dot" style="background:'+c2+'"></div>'+p2.n
      +' <span style="font-weight:700;color:'+c2+';margin-left:5px;">'+p2.v+'%</span></div>';
  });
  document.getElementById('dp-tabs').innerHTML=tabs;

  // ===== BODY ===== triage view: failing indicators first (auto-expanded), then passing
  var body='';

  if(!inds.length){
    body+='<div class="ind-empty">No indicators defined for this building block.</div>';
  } else {
    // -- NEEDS REVIEW section: failing indicators with their full readout open --
    if(review.length){
      body+='<div class="dp-sec dp-sec-review">Needs Review <span class="dp-sec-count">'+review.length+'</span></div>';
      review.forEach(function(c){
        body+=renderIndicatorCard(c.ind, /*autoExpand=*/true);
      });
    }

    // -- PASSING section (or just INDICATORS if no review): collapsed mini-rows --
    if(passing.length){
      var passHeader = review.length ? 'Passing' : 'Indicators';
      body+='<div class="dp-sec">'+passHeader+' <span class="dp-sec-count">'+passing.length+'</span></div>';
      passing.forEach(function(c){
        body+=renderIndicatorCard(c.ind, /*autoExpand=*/false);
      });
    } else if(review.length){
      // edge case: all indicators need review, no passing — no second section needed
    }

    // -- Empty state: zero indicators needing review (unusual but possible) --
    if(!review.length && !passing.length){
      body+='<div class="ind-empty">No indicators defined for this building block.</div>';
    }
  }

  // -- RULES disclosure at the bottom (collapsed by default; most users don't need it) --
  if(bb.ruleNotes && bb.ruleNotes.length){
    var rulesId = 'rules-' + Math.floor(Math.random()*1e6);
    body+='<button class="bb-rules-toggle" onclick="toggleBBRules(this,\''+rulesId+'\')" type="button">'
      +    '<svg viewBox="0 0 8 8" fill="none"><path d="M2.5 1.5L6 4L2.5 6.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>'
      +    '<span>Rules &amp; notes</span>'
      +    '<span class="bb-rules-count">'+bb.ruleNotes.length+'</span>'
      +  '</button>';
    body+='<div class="bb-rules" id="'+rulesId+'">';
    bb.ruleNotes.forEach(function(r){
      body+='<div class="bb-rule">'
        +'<div class="bb-rule-lbl">Rule</div>'
        +'<div class="bb-rule-body">'+r+'</div>'
        +'</div>';
    });
    body+='</div>';
  }

  document.getElementById('dp-body').innerHTML=body;
}

// Classify a single indicator: derive its state, score, current band, and a
// boolean indicating whether it warrants the user's attention.
// Triage rule: needs review when state !== 'good' OR ind.alert is set.
function classifyIndicator(ind){
  var curBand = null;
  if(ind.grades && ind.grades.length){
    for(var i=0; i<ind.grades.length; i++){
      if(ind.grades[i].current){ curBand = ind.grades[i]; break; }
    }
  }
  var hasScore = curBand && typeof curBand.s === 'number';
  var score = hasScore ? curBand.s : null;

  var state = 'neutral';
  if(hasScore){
    if(score >= 85) state = 'good';
    else if(score >= 60) state = 'warn';
    else state = 'crit';
  }
  if(ind.alert) state = (state==='crit' ? 'crit' : 'warn');
  if(curBand && curBand.flag && score !== null && score < 60) state = 'crit';

  var needsReview = (state !== 'good') || !!ind.alert;
  return {state:state, score:score, curBand:curBand, needsReview:needsReview};
}

// Toggle the Rules disclosure
function toggleBBRules(btn, id){
  var panel = document.getElementById(id);
  if(!panel) return;
  var isOpen = panel.classList.toggle('open');
  btn.classList.toggle('open', isOpen);
}

// Render an indicator in the new collapsible flat-block shape.
// At rest: head row (name on left, state cluster on right) + current band readout below.
// On click (or chevron): expand description + meta rows + "All bands" toggle.
//
// State + score derived from the band marked current:true.
// State cluster mirrors the deliverable-row pattern: dot · state-label · score, all in the state colour.
//
// autoExpand=true renders the block with the .expanded class already on it (used by
// the triage view to show failing indicators with their full readout open at panel load).
function renderIndicatorCard(ind, autoExpand){
  // Resolve current band, score, and state from grades
  var curBand = null;
  if(ind.grades && ind.grades.length){
    for(var gi=0; gi<ind.grades.length; gi++){
      if(ind.grades[gi].current){ curBand = ind.grades[gi]; break; }
    }
  }
  var hasScore = curBand && typeof curBand.s === 'number';
  var indScore = hasScore ? curBand.s : null;

  // State derived from score band; alert and flag can escalate
  var indState = 'neutral';
  if(hasScore){
    if(indScore >= 85) indState = 'good';
    else if(indScore >= 60) indState = 'warn';
    else indState = 'crit';
  }
  if(ind.alert) indState = (indState==='crit' ? 'crit' : 'warn');
  if(curBand && curBand.flag && indScore !== null && indScore < 60) indState = 'crit';

  var blockId = 'ind-' + (ind.name||'').toLowerCase().replace(/[^a-z0-9]+/g,'-') + '-' + Math.floor(Math.random()*1e6);
  var bandsId = blockId + '-bands';
  var descId  = blockId + '-desc';
  var srcId   = blockId + '-src';

  var blockClasses = 'ind-block state-' + indState + (autoExpand ? ' expanded' : '');
  var h = '<div class="'+blockClasses+'" id="'+blockId+'" onclick="toggleIndExpand(this, event)">';

  // -- HEAD: name on left, info+source icons inline, dot+score cluster on right --
  // The state label ("Passing"/"Review"/"Action") is intentionally absent:
  // the dot colour and the score colour both carry the state, and section
  // headers ("Needs Review" / "Passing" / "Indicators") provide grouping context.
  // The info icon toggles a description tooltip; the source icon toggles a
  // provenance tooltip. Both are independent of the block's expansion state.
  h += '<div class="ind-head">'
    +    '<div class="ind-name-wrap">'
    +      '<div class="ind-name">'+ind.name+'</div>';
  // Info (description) icon — only when ind.desc exists
  if(ind.desc){
    h +=   '<button class="ind-info-btn" onclick="event.stopPropagation(); toggleIndDesc(\''+descId+'\', this)" title="What this measures" aria-label="What this measures" type="button">'
      +      '<svg viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="4.7" stroke="currentColor" stroke-width=".7"/><circle cx="6" cy="3.7" r=".55" fill="currentColor"/><path d="M6 5.6V8.6" stroke="currentColor" stroke-width=".7" stroke-linecap="round"/></svg>'
      +    '</button>';
  }
  // Source (provenance) icon — only when ind.sources has entries
  if(ind.sources && ind.sources.length){
    h +=   '<button class="ind-info-btn ind-src-btn" onclick="event.stopPropagation(); toggleIndSrc(\''+srcId+'\', this)" title="Source files" aria-label="Source files" type="button">'
      +      '<svg viewBox="0 0 12 12" fill="none"><path d="M1.5 3.2C1.5 2.7 1.9 2.3 2.4 2.3H4.7L5.6 3.3H9.6C10.1 3.3 10.5 3.7 10.5 4.2V9.0C10.5 9.5 10.1 9.9 9.6 9.9H2.4C1.9 9.9 1.5 9.5 1.5 9.0V3.2Z" stroke="currentColor" stroke-width=".7" stroke-linejoin="round"/></svg>'
      +    '</button>';
  }
  h +=     '<span class="ind-expand-chev">'
    +        '<svg viewBox="0 0 8 8" fill="none"><path d="M2.5 1.5L6 4L2.5 6.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    +      '</span>'
    +    '</div>'
    +    '<div class="ind-state">'
    +      '<span class="ind-dot dot-'+indState+'"></span>';
  if(hasScore){
    h +=     '<span class="ind-score">'+indScore+'<span class="ind-score-pct">%</span></span>';
  }
  h +=   '</div>'
    + '</div>';

  // -- CURRENT BAND READOUT: visible at rest below the head --
  if(curBand){
    h += '<div class="ind-current">'
      +    '<span class="ind-current-tag">'+curBand.l+'</span>'
      +    '<span class="ind-current-thresh">&mdash; '+curBand.r+'</span>'
      +  '</div>';
  }

  // -- INFO TOOLTIP: description prose, toggled by the info icon, independent of expansion --
  if(ind.desc){
    h += '<div class="ind-tooltip ind-tooltip-desc" id="'+descId+'">'
      +    '<div class="ind-tooltip-body">'+ind.desc+'</div>'
      +  '</div>';
  }

  // -- SOURCE TOOLTIP: provenance file list, toggled by the source icon --
  if(ind.sources && ind.sources.length){
    h += '<div class="ind-tooltip ind-tooltip-src" id="'+srcId+'">'
      +    '<div class="ind-tooltip-k">Sourced from</div>'
      +    '<div class="ind-tooltip-v">'+ind.sources.join(', ')+'</div>'
      +  '</div>';
  }

  // -- EXPANDED BODY: rec, alert, all bands (description and source moved out) --
  var hasExpandedContent = ind.rec || ind.alert || (ind.grades && ind.grades.length > 1);
  if(hasExpandedContent){
    h += '<div class="ind-expanded">';

    // Meta rows: recommendation, alert (description and source moved to tooltips)
    var metaRows = '';
    if(ind.rec){
      metaRows += '<div class="ind-meta-k">Note</div>'
        +  '<div class="ind-meta-v is-rec">'+ind.rec+'</div>';
    }
    if(ind.alert){
      metaRows += '<div class="ind-meta-k">Alert</div>'
        +  '<div class="ind-meta-v is-alert">'+ind.alert+'</div>';
    }
    if(metaRows){
      h += '<div class="ind-meta">'+metaRows+'</div>';
    }

    // "All bands" toggle — only when multiple bands exist
    if(ind.grades && ind.grades.length > 1){
      h += '<button class="ind-bands-toggle" onclick="event.stopPropagation(); toggleIndBands(this, \''+bandsId+'\')" type="button">'
        +    '<svg viewBox="0 0 8 8" fill="none"><path d="M2.5 1.5L6 4L2.5 6.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        +    '<span class="ind-bands-toggle-label">All bands</span>'
        +  '</button>';
      h += '<div class="ind-bands" id="'+bandsId+'">';
      ind.grades.forEach(function(gr){
        var rowCls = gr.current ? 'ind-band-row current' : 'ind-band-row';
        h += '<div class="'+rowCls+'">'
          +    '<div class="ind-band-l">'+gr.l+'</div>'
          +    '<div class="ind-band-r">'+gr.r+'</div>'
          +  '</div>';
      });
      h += '</div>';
    }

    h += '</div>';
  }

  h += '</div>';
  return h;
}

// Toggle expansion of an indicator block.
// Auto-expands the block on click of any part except the bands toggle, info icon,
// or source icon (each of which has its own stopPropagation, but guarded here too).
function toggleIndExpand(blockEl, evt){
  if(evt && evt.target && evt.target.closest){
    if(evt.target.closest('.ind-bands-toggle')) return;
    if(evt.target.closest('.ind-info-btn')) return;
    if(evt.target.closest('.ind-tooltip')) return;
  }
  blockEl.classList.toggle('expanded');
}

// Toggle the description tooltip on an indicator.
// Independent of the block's expansion state — the user can read the description
// without expanding the indicator, and vice versa.
function toggleIndDesc(id, btn){
  var tip = document.getElementById(id);
  if(!tip) return;
  var isOpen = tip.classList.toggle('open');
  if(btn) btn.classList.toggle('open', isOpen);
}

// Toggle the source-provenance tooltip on an indicator.
function toggleIndSrc(id, btn){
  var tip = document.getElementById(id);
  if(!tip) return;
  var isOpen = tip.classList.toggle('open');
  if(btn) btn.classList.toggle('open', isOpen);
}

// Toggle a section-header tooltip (e.g. "Where this file goes" prose).
// Same pattern as the indicator tooltips; the section header carries an info
// icon, clicking it reveals the section's descriptive prose below.
function toggleSecInfo(id, btn){
  var tip = document.getElementById(id);
  if(!tip) return;
  var isOpen = tip.classList.toggle('open');
  if(btn) btn.classList.toggle('open', isOpen);
}

// Toggle the "All bands" panel below an indicator
function toggleIndBands(btn, id){
  var panel = document.getElementById(id);
  if(!panel) return;
  var isOpen = panel.classList.toggle('open');
  btn.classList.toggle('open', isOpen);
  var lbl = btn.querySelector('.ind-bands-toggle-label');
  if(lbl) lbl.textContent = isOpen ? 'Hide bands' : 'All bands';
}

// ============================================================
// DELIVERABLE PANEL — mirrors buildPanel but for deliverable files
// ============================================================
function buildDelPanel(gi, di){
  var grp = DELIVERABLE_ONTOLOGY.groups[gi];
  var d   = grp.items[di];
  var tier = tierFor(d.score);
  var col  = sCol(d.score);

  document.getElementById('dp-chip').textContent = grp.short + ' Deliverable';
  document.getElementById('dp-pname').textContent = d.name;
  var gsubEl = document.getElementById('dp-gsub');
  gsubEl.textContent = d.fmt + '  \u00b7  ' + d.sz;
  gsubEl.style.display = '';

  var bs = document.getElementById('dp-bscore');
  bs.innerHTML = d.score + '<span class="dp-bpct">%</span>';
  bs.style.color = col;

  // Hero status text dropped — the score colour carries the state, and the
  // Grade and authorisation section below carries the tier-grade explanation.
  var statusEl = document.getElementById('dp-status');
  statusEl.textContent = '';
  statusEl.style.display = 'none';

  // Sibling tabs: all deliverables in the same group
  var tabs = '';
  grp.items.forEach(function(d2, i){
    var c2 = sCol(d2.score);
    tabs += '<div class="dp-tab' + (i===di?' active':'') + '" onclick="switchDeliverable(' + gi + ',' + i + ')">'
      + '<div class="dp-tab-dot" style="background:' + c2 + '"></div>' + d2.name
      + ' <span style="font-weight:700;color:' + c2 + ';margin-left:5px;">' + d2.score + '%</span></div>';
  });
  document.getElementById('dp-tabs').innerHTML = tabs;

  // ===== BODY ===== About section dropped — the deliverable name + format/size
  // sub-line + tier grade below carry the identity. About prose was documentation.
  var body = '';

  // "Where this file goes" — useful but not load-bearing for grade decisions.
  // Section header carries an info icon; clicking reveals the prose tooltip.
  var travelsId = 'sec-travels-' + Math.floor(Math.random()*1e6);
  body += '<div class="dp-sec">Where this file goes'
       +    '<button class="dp-sec-info" onclick="toggleSecInfo(\''+travelsId+'\', this)" type="button" title="View detail" aria-label="View detail">'
       +      '<svg viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="4.7" stroke="currentColor" stroke-width=".7"/><circle cx="6" cy="3.7" r=".55" fill="currentColor"/><path d="M6 5.6V8.6" stroke="currentColor" stroke-width=".7" stroke-linecap="round"/></svg>'
       +    '</button>'
       +  '</div>'
       +  '<div class="dp-sec-tooltip" id="'+travelsId+'">'
       +    '<div class="dp-sec-tooltip-body">' + d.travelsTo + '</div>'
       +  '</div>';

  // Grade and authorisation — tier name + range always visible (load-bearing
  // for the at-a-glance grade), but the authorisation prose ("Authorised for...")
  // moves behind an info icon on the section header.
  var authId = 'sec-auth-' + Math.floor(Math.random()*1e6);
  body += '<div class="dp-sec">Grade and authorisation'
       +    '<button class="dp-sec-info" onclick="toggleSecInfo(\''+authId+'\', this)" type="button" title="View authorisation detail" aria-label="View authorisation detail">'
       +      '<svg viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="4.7" stroke="currentColor" stroke-width=".7"/><circle cx="6" cy="3.7" r=".55" fill="currentColor"/><path d="M6 5.6V8.6" stroke="currentColor" stroke-width=".7" stroke-linecap="round"/></svg>'
       +    '</button>'
       +  '</div>'
       +  '<div class="del-grade">'
       +    '<div class="del-grade-row">'
       +      '<span class="del-grade-badge" style="color:' + col + ';">' + tier.name + '</span>'
       +      '<span class="del-grade-range">' + tier.range + '</span>'
       +    '</div>'
       +  '</div>'
       +  '<div class="dp-sec-tooltip" id="'+authId+'">'
       +    '<div class="dp-sec-tooltip-body">' + (d.tierAuth[tier.name] || '') + '</div>'
       +  '</div>';

  // Quality checks — triage view: failing checks auto-expand, passing collapsed
  if(!d.checks || !d.checks.length){
    body += '<div class="dp-sec">Quality checks</div>';
    body += '<div class="ind-empty">No quality checks defined for this deliverable.</div>';
  } else {
    var classifiedChecks = d.checks.map(function(chk){ return {ind:chk, info:classifyIndicator(chk)}; });
    var reviewChecks  = classifiedChecks.filter(function(c){ return c.info.needsReview; });
    var passingChecks = classifiedChecks.filter(function(c){ return !c.info.needsReview; });
    reviewChecks.sort(function(a,b){
      var stOrder = {crit:0, warn:1, good:2, neutral:3};
      var dd = stOrder[a.info.state] - stOrder[b.info.state];
      if(dd!==0) return dd;
      var as = (a.info.score==null) ? 999 : a.info.score;
      var bs = (b.info.score==null) ? 999 : b.info.score;
      return as - bs;
    });
    if(reviewChecks.length){
      body += '<div class="dp-sec dp-sec-review">Needs Review <span class="dp-sec-count">'+reviewChecks.length+'</span></div>';
      reviewChecks.forEach(function(c){
        body += renderIndicatorCard(c.ind, /*autoExpand=*/true);
      });
    }
    if(passingChecks.length){
      var passHeader = reviewChecks.length ? 'Passing' : 'Quality checks';
      body += '<div class="dp-sec">'+passHeader+' <span class="dp-sec-count">'+passingChecks.length+'</span></div>';
      passingChecks.forEach(function(c){
        body += renderIndicatorCard(c.ind, /*autoExpand=*/false);
      });
    }
  }

  document.getElementById('dp-body').innerHTML = body;
}

// ============================================================
// DELIVERABLES VIEW
// ============================================================

// =============================================================
// DELIVERABLE SCORES ONTOLOGY
// 16 deliverable confidence scores from cbmi_master_ontology.yaml,
// rendered in plain English. Mirrors the structure of the BB ontology
// but framed around files-as-output (where they go, what grade they
// earned, what authorisations the grade carries).
//
// Tier table is the Gold/Silver/Bronze/Marginal/Poor ladder from the
// ontology with the application authorisations attached.
// =============================================================

// Universal tier ladder applied to every deliverable
var DEL_TIERS = [
  {name:'Gold',     range:'90 to 100', minScore:90},
  {name:'Silver',   range:'75 to 89',  minScore:75},
  {name:'Bronze',   range:'60 to 74',  minScore:60},
  {name:'Marginal', range:'40 to 59',  minScore:40},
  {name:'Poor',     range:'0 to 39',   minScore:0}
];

function tierFor(score){
  for(var i=0; i<DEL_TIERS.length; i++){
    if(score >= DEL_TIERS[i].minScore) return DEL_TIERS[i];
  }
  return DEL_TIERS[DEL_TIERS.length-1];
}

var DELIVERABLE_ONTOLOGY = {
  groups: [
    {
      id:'cap-del', name:'Capture Deliverables', short:'Capture',
      col:'#4db896',
      desc:'Files handed off from Stage 2 pre-processing to the Processing universe.',
      items: [

        {id:'cal', name:'Camera Calibration',
         fmt:'XML', sz:'18 KB',
         score:95,
         travelsTo:'Camera calibration file travels with the image set to the Processing universe.',
         desc:'The camera calibration file the drone manufacturer provides for the sensor used in this survey. Quality reflects how well the calibration matches the field camera and how recent it is.',
         tierAuth:{
           Gold:'Approved for all reconstruction workflows including precision engineering and regulatory work.',
           Silver:'Approved for survey-grade reconstruction. Minor calibration drift noted.',
           Bronze:'Acceptable for mapping-grade reconstruction. Self-calibration in the reconstruction step will partly compensate.',
           Marginal:'Reconstruction will run but may carry geometric distortion. Consider a recalibration before the next survey.',
           Poor:'Recalibration required. Reconstruction outputs cannot be certified at any grade.'
         },
         checks:[
          {name:'Calibration Source',
           desc:'Whether the file came from the manufacturer or a field calibration.',
           grades:[
            {l:'Manufacturer file',  r:'Sealed factory calibration',     s:100, current:true},
            {l:'Recent field cal',   r:'Operator-run, under 6 months',   s:80},
            {l:'Older field cal',    r:'Operator-run, 6 to 18 months',   s:55},
            {l:'No documented source',r:'Source unknown',                s:25}
           ],
           sources:['Camera calibration file','Drone make and model'],
           rec:'Calibration came from the manufacturer.', alert:null},
          {name:'Camera Match',
           desc:'Whether the camera used in the field matches the camera described in the calibration file.',
           grades:[
            {l:'Both match', r:'Make and model match',  s:100, current:true},
            {l:'Make only',  r:'Make matches, model differs', s:60},
            {l:'No match',   r:'Neither matches',       s:20}
           ],
           sources:['Camera calibration file','Drone provenance log'],
           rec:'Calibration is the right one for this camera.', alert:null},
          {name:'Calibration Age',
           desc:'How long ago the calibration was performed.',
           grades:[
            {l:'Fresh',       r:'Under 12 months',  s:100, current:true},
            {l:'Acceptable',  r:'12 to 24 months',  s:75},
            {l:'Aging',       r:'24 to 36 months',  s:50},
            {l:'Stale',       r:'Over 36 months',   s:20}
           ],
           sources:['Camera calibration file'],
           rec:'Calibration is within the freshness window.', alert:null}
         ]},

        {id:'geo', name:'Geotagged Images',
         fmt:'JPEG with EXIF', sz:'2.84 GB (2,841 frames)',
         score:92,
         travelsTo:'Geotagged image folder travels to the Processing universe as the primary reconstruction input.',
         desc:'The image set with refined GNSS positions embedded in EXIF metadata. Every image carries a coordinate refined by post-processed kinematic correction.',
         tierAuth:{
           Gold:'Approved for full reconstruction including survey-grade and engineering deliverables.',
           Silver:'Approved for survey-grade reconstruction. Minor positioning gaps documented.',
           Bronze:'Acceptable for mapping-grade reconstruction. Some images may need fallback positioning.',
           Marginal:'Reconstruction runs but coverage and positioning gaps will limit output accuracy.',
           Poor:'Image set is not fit to reconstruct. Re-fly recommended.'
         },
         checks:[
          {name:'Positioning Accuracy',
           desc:'Share of images that received a precision-refined coordinate.',
           grades:[
            {l:'Excellent',  r:'95% or more',  s:100, current:true},
            {l:'Strong',     r:'85% or more',  s:85},
            {l:'Acceptable', r:'70% or more',  s:65},
            {l:'Marginal',   r:'less than 70%',s:30, flag:'Positioning gaps'}
           ],
           sources:['PPK trajectory file','Image EXIF metadata'],
           rec:'Every image has a precision coordinate.', alert:null},
          {name:'Image Integrity',
           desc:'Share of images that are not corrupted, blurred beyond recognition, or otherwise unreadable.',
           grades:[
            {l:'Excellent', r:'99% or more',  s:100},
            {l:'Strong',    r:'95% or more',  s:75, current:true},
            {l:'Critical',  r:'less than 95%',s:30, flag:'Invalid images present'}
           ],
           sources:['Image quality log'],
           rec:'Image integrity is strong.', alert:null},
          {name:'Geotag Completeness',
           desc:'Share of images that arrived with embedded GPS coordinates in EXIF.',
           grades:[
            {l:'Complete',  r:'Every image',   s:100, current:true},
            {l:'Most',      r:'97% or more',   s:72},
            {l:'Incomplete',r:'less than 97%', s:25, flag:'Geotagging incomplete'}
           ],
           sources:['Image EXIF metadata'],
           rec:'Geotagging is complete across the image set.', alert:null},
          {name:'Photo Overlap',
           desc:'Forward and side overlap of the image set. Drives reconstruction quality.',
           grades:[
            {l:'Excellent', r:'Forward 80% and side 70%', s:100},
            {l:'Strong',    r:'Forward 70% and side 60%', s:75, current:true},
            {l:'Marginal',  r:'Below the Strong band',    s:35, flag:'Inadequate overlap'}
           ],
           sources:['Mission plan','Drone provenance log'],
           rec:'Overlap meets the Strong band. Consider lifting to the Excellent band on dense-feature surveys.',
           alert:null},
          {name:'Image Format',
           desc:'Whether the image format suits photogrammetric reconstruction.',
           grades:[
            {l:'Raw',           r:'DNG or RAW',          s:100},
            {l:'High-quality JPG',r:'JPG at high quality',s:70, current:true},
            {l:'Compressed JPG',r:'JPG with strong compression', s:40}
           ],
           sources:['Image EXIF metadata'],
           rec:'High-quality JPG is suitable for survey work.', alert:null}
         ]},

        {id:'gcp', name:'Control Point Coordinate File',
         fmt:'CSV', sz:'4.2 KB (12 points)',
         score:78,
         travelsTo:'Ground control coordinates travel to the Processing universe as the survey control.',
         desc:'The list of surveyed ground control points used to anchor the reconstruction to absolute coordinates.',
         tierAuth:{
           Gold:'Approved for survey-grade and engineering control. Suitable for regulatory and legal boundary work.',
           Silver:'Approved for engineering control. Minor accuracy or distribution limitations documented.',
           Bronze:'Acceptable as mapping-grade control. Not approved for survey-grade certification.',
           Marginal:'Control is weak. Reconstruction may run but absolute accuracy will be limited.',
           Poor:'Control is unfit. More ground points or a re-survey required.'
         },
         checks:[
          {name:'Horizontal Accuracy',
           desc:'Average horizontal accuracy of the ground control points.',
           grades:[
            {l:'Survey-grade',r:'Under 2 cm',  s:100},
            {l:'Engineering', r:'Under 5 cm',  s:88, current:true},
            {l:'Mapping',     r:'Under 10 cm', s:65},
            {l:'Reject',      r:'10 cm or more',s:35, flag:'Low horizontal accuracy'}
           ],
           sources:['Control Point coordinate file','Stage 2 known-point report'],
           rec:'Horizontal accuracy is at engineering grade.', alert:null},
          {name:'Vertical Accuracy',
           desc:'Average vertical accuracy of the ground control points.',
           grades:[
            {l:'Survey-grade',r:'Under 3 cm',  s:100},
            {l:'Engineering', r:'Under 8 cm',  s:88, current:true},
            {l:'Mapping',     r:'Under 15 cm', s:65},
            {l:'Reject',      r:'15 cm or more',s:35, flag:'Low vertical accuracy'}
           ],
           sources:['Control Point coordinate file','Stage 2 known-point report'],
           rec:'Vertical accuracy is at engineering grade.', alert:null},
          {name:'Point Count',
           desc:'Number of surveyed ground control points available to the reconstruction.',
           grades:[
            {l:'Ample',       r:'7 or more',   s:100},
            {l:'Adequate',    r:'5 to 6',      s:88, current:true},
            {l:'Sparse',      r:'3 to 4',      s:65},
            {l:'Insufficient',r:'Fewer than 3',s:0,  flag:'Insufficient control points'}
           ],
           sources:['Control Point coordinate file'],
           rec:'Twelve control points support the survey area.', alert:null},
          {name:'Network Distribution',
           desc:'How well the ground control points are spread across the area of interest.',
           grades:[
            {l:'Even',      r:'Boundary covered with strong spacing', s:100},
            {l:'Adequate',  r:'Boundary covered with closer spacing', s:85},
            {l:'Skewed',    r:'Quadrant under-served or clustered',   s:25, current:true, flag:'Network skewed'}
           ],
           sources:['Control Point coordinate file','Area of interest boundary'],
           rec:'Add two control points in the NE quadrant before certifying survey-grade.',
           alert:'NE quadrant has only one control point within a 200 m radius.'}
         ]},

        {id:'chk', name:'Check Point File',
         fmt:'CSV', sz:'2.1 KB (6 points)',
         score:88,
         travelsTo:'Check points travel to the Processing universe and are held back from reconstruction for independent verification.',
         desc:'A set of surveyed points held back from the reconstruction. The accuracy report measures the survey against these to give an independent grade.',
         tierAuth:{
           Gold:'Approved as independent reference for regulatory and legal accuracy claims.',
           Silver:'Approved as independent reference for engineering accuracy claims.',
           Bronze:'Acceptable for mapping-grade verification. Not approved for regulatory work.',
           Marginal:'Verification possible but the resulting accuracy claim will carry caveats.',
           Poor:'Check points are unfit for independent verification.'
         },
         checks:[
          {name:'Horizontal Accuracy',
           desc:'Average horizontal accuracy of the check points themselves.',
           grades:[
            {l:'Survey-grade',r:'Under 2 cm',  s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',  s:88},
            {l:'Mapping',     r:'Under 10 cm', s:65},
            {l:'Reject',      r:'10 cm or more',s:35}
           ],
           sources:['Check point file'],
           rec:'Check points are survey-grade.', alert:null},
          {name:'Vertical Accuracy',
           desc:'Average vertical accuracy of the check points themselves.',
           grades:[
            {l:'Survey-grade',r:'Under 3 cm',  s:100, current:true},
            {l:'Engineering', r:'Under 8 cm',  s:88},
            {l:'Mapping',     r:'Under 15 cm', s:65},
            {l:'Reject',      r:'15 cm or more',s:35}
           ],
           sources:['Check point file'],
           rec:'Vertical accuracy supports a survey-grade claim.', alert:null},
          {name:'Point Count',
           desc:'Number of check points held back for independent verification.',
           grades:[
            {l:'Robust',      r:'10 or more',  s:100},
            {l:'Adequate',    r:'5 to 9',      s:88, current:true},
            {l:'Sparse',      r:'3 to 4',      s:65},
            {l:'Inadequate',  r:'2',           s:35, flag:'Insufficient check points'},
            {l:'None',        r:'1 or fewer',  s:0,  flag:'No check points'}
           ],
           sources:['Check point file'],
           rec:'Six check points support statistical validation.', alert:null}
         ]}
      ]
    },

    {
      id:'pro-del', name:'Processing Deliverables', short:'Processing',
      col:'#5596cc',
      desc:'Files handed off from photogrammetric processing to the Analytics universe and to the client.',
      items: [

        {id:'pc', name:'Point Cloud',
         fmt:'LAS 1.4 / LAZ', sz:'3.4 GB (148 million points)',
         score:91,
         travelsTo:'Point cloud travels to the Analytics universe.',
         desc:'The dense three-dimensional reconstruction of the surveyed area, with ground and non-ground points classified.',
         tierAuth:{
           Gold:'Approved for engineering-grade measurement, volume work, and downstream analytics.',
           Silver:'Approved for engineering-grade measurement. Minor density or classification limitations documented.',
           Bronze:'Acceptable for mapping-grade measurement. Not approved for precision engineering.',
           Marginal:'Usable for visual reference and rough measurement. Quantitative work will carry large uncertainty.',
           Poor:'Cloud is unfit for measurement.'
         },
         checks:[
          {name:'Density',
           desc:'Average number of points per square metre of the surveyed area.',
           grades:[
            {l:'High',   r:'40 or more',  s:100, current:true},
            {l:'Survey', r:'25 or more',  s:85},
            {l:'Mapping',r:'10 or more',  s:60},
            {l:'Low',    r:'Fewer than 10',s:25, flag:'Low density'}
           ],
           sources:['Point cloud statistics'],
           rec:'Density supports volume work.', alert:null},
          {name:'Coverage',
           desc:'Share of the area of interest covered by valid points.',
           grades:[
            {l:'Complete',  r:'99% or more',s:100, current:true},
            {l:'Near-complete',r:'95% or more',s:80},
            {l:'Partial',   r:'85% or more',s:50},
            {l:'Patchy',    r:'less than 85%',s:20, flag:'Cloud coverage gaps'}
           ],
           sources:['Point cloud statistics'],
           rec:'Coverage is complete across the AOI.', alert:null},
          {name:'Classification Quality',
           desc:'How reliably points were sorted into ground and non-ground for downstream analytics.',
           grades:[
            {l:'Confident', r:'85% or more',  s:100},
            {l:'Probable',  r:'70% or more',  s:75, current:true},
            {l:'Uncertain', r:'50% or more',  s:45},
            {l:'Unreliable',r:'less than 50%',s:15, flag:'Classification uncertain'}
           ],
           sources:['Point cloud file','Reconstruction report'],
           rec:'Bench 3 classification is provisional; revisit alongside the reconstruction re-tie.',
           alert:'Bench 3 classification confidence is below 80%.'},
          {name:'Noise Level',
           desc:'Share of stray points sitting outside the reconstructed surface.',
           grades:[
            {l:'Low',      r:'Under 1%',   s:100},
            {l:'Acceptable',r:'Under 3%',  s:88, current:true},
            {l:'High',     r:'Under 6%',   s:50},
            {l:'Severe',   r:'6% or more', s:20}
           ],
           sources:['Point cloud statistics'],
           rec:'Noise level is acceptable.', alert:null}
         ]},

        {id:'dsm', name:'Surface Model',
         fmt:'GeoTIFF (Float32)', sz:'1.8 GB at 10 cm grid',
         score:93,
         travelsTo:'Surface model travels to the Analytics universe.',
         desc:'A continuous raster of the top-of-surface elevation, including buildings, vegetation and other features. Resolves to its final grade once the accuracy report lands.',
         tierAuth:{
           Gold:'Approved for survey-grade and engineering surface analysis.',
           Silver:'Approved for engineering surface analysis. Minor accuracy limitations documented.',
           Bronze:'Acceptable for mapping-grade surface analysis. Not approved for precision engineering.',
           Marginal:'Usable for visual context. Quantitative analysis will carry significant uncertainty.',
           Poor:'Surface model is unfit for measurement.'
         },
         checks:[
          {name:'No-Data Coverage',
           desc:'Share of the raster covered by valid measurements.',
           grades:[
            {l:'Complete',  r:'99% or more',s:100, current:true},
            {l:'Near-complete',r:'95% or more',s:85},
            {l:'Partial',   r:'85% or more',s:55},
            {l:'Gaps',      r:'less than 85%',s:20, flag:'No-data gaps'}
           ],
           sources:['Surface model raster'],
           rec:'No gaps detected in the surface raster.', alert:null},
          {name:'Resolution',
           desc:'Cell size of the surface raster.',
           grades:[
            {l:'High',  r:'5 cm or less',  s:100},
            {l:'Survey',r:'10 cm or less', s:90, current:true},
            {l:'Mapping',r:'25 cm or less',s:60},
            {l:'Coarse',r:'Over 25 cm',    s:30}
           ],
           sources:['Surface model raster metadata'],
           rec:'Resolution is at the survey band.', alert:null},
          {name:'Vertical Accuracy',
           desc:'Vertical accuracy verified against the independent check points.',
           grades:[
            {l:'Professional',r:'Under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Reject',      r:'10 cm or more',s:20, flag:'Vertical accuracy out of spec'}
           ],
           sources:['Check point file','Accuracy report'],
           rec:'Vertical accuracy meets professional grade.', alert:null}
         ]},

        {id:'dtm', name:'Bare-Earth Surface',
         fmt:'GeoTIFF (Float32)', sz:'1.6 GB at 10 cm grid',
         score:85,
         travelsTo:'Bare-earth surface travels to the Analytics universe as the foundation for every volume and cut/fill calculation.',
         desc:'A continuous raster of the bare-earth surface with buildings, vegetation and other above-ground features removed. The single most important input to mining analytics.',
         tierAuth:{
           Gold:'Approved for survey-grade volume work, regulatory reporting and engineering design.',
           Silver:'Approved for engineering-grade volume work. Mining analytics will run with confidence.',
           Bronze:'Acceptable for mapping-grade volume work with disclosed uncertainty. Not for regulatory submission without caveats.',
           Marginal:'Volume work runs but carries large uncertainty. Disclose to the client before business decisions.',
           Poor:'Bare-earth surface is unfit for volume work.'
         },
         checks:[
          {name:'No-Data Coverage',
           desc:'Share of the raster covered by valid measurements after vegetation and structures were removed.',
           grades:[
            {l:'Complete',  r:'95% or more', s:100},
            {l:'Strong',    r:'90% or more', s:85, current:true},
            {l:'Partial',   r:'80% or more', s:55},
            {l:'Gaps',      r:'less than 80%',s:20}
           ],
           sources:['Bare-earth surface raster'],
           rec:'Coverage is strong across the AOI.', alert:null},
          {name:'Resolution',
           desc:'Cell size of the bare-earth raster.',
           grades:[
            {l:'High',  r:'5 cm or less',  s:100},
            {l:'Survey',r:'10 cm or less', s:90, current:true},
            {l:'Mapping',r:'25 cm or less',s:60},
            {l:'Coarse',r:'Over 25 cm',    s:30}
           ],
           sources:['Bare-earth surface raster metadata'],
           rec:'Resolution is at the survey band.', alert:null},
          {name:'Vertical Accuracy',
           desc:'Vertical accuracy verified against the independent check points.',
           grades:[
            {l:'Professional',r:'Under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Reject',      r:'10 cm or more',s:20}
           ],
           sources:['Check point file','Accuracy report'],
           rec:'Vertical accuracy meets professional grade.', alert:null},
          {name:'Interpolation Footprint',
           desc:'Share of the surface that was interpolated to fill gaps left by vegetation, structures or weak point coverage.',
           grades:[
            {l:'Tight',  r:'Under 5%',  s:100},
            {l:'Modest', r:'Under 15%', s:80, current:true, flag:'Some interpolation on Bench 3'},
            {l:'Wide',   r:'Under 30%', s:50},
            {l:'Severe', r:'30% or more',s:20}
           ],
           sources:['Bare-earth surface raster'],
           rec:'Bench 3 carries some interpolation. Consider manual cleanup before engineering use.',
           alert:'Bench 3 residual vegetation leaves visible interpolation footprint.'}
         ]},

        {id:'ort', name:'Orthophoto',
         fmt:'GeoTIFF (Cloud Optimised)', sz:'4.1 GB at 4.8 cm pixel',
         score:93,
         travelsTo:'Orthophoto travels to the Analytics universe and is delivered to the client.',
         desc:'A photographic top-down image of the surveyed area, geometrically corrected so every pixel sits on its true ground position.',
         tierAuth:{
           Gold:'Approved for client delivery, visual reference at all grades, and spatial measurement against pixel positions.',
           Silver:'Approved for client delivery and engineering visual reference.',
           Bronze:'Acceptable for mapping-grade visual reference.',
           Marginal:'Visual reference only. Measurement against pixel positions will carry visible uncertainty.',
           Poor:'Orthophoto is unfit for delivery.'
         },
         checks:[
          {name:'Resolution',
           desc:'Pixel size of the orthomosaic on the ground.',
           grades:[
            {l:'High',  r:'3 cm or less',  s:100},
            {l:'Survey',r:'5 cm or less',  s:90, current:true},
            {l:'Mapping',r:'10 cm or less',s:60},
            {l:'Coarse',r:'Over 10 cm',    s:30}
           ],
           sources:['Orthophoto raster metadata'],
           rec:'Pixel size is in the survey band.', alert:null},
          {name:'Visual Quality',
           desc:'Visible artefacts in the orthomosaic such as seams, ghosting or motion blur.',
           grades:[
            {l:'Clean',     r:'No visible artefacts', s:100},
            {l:'Acceptable',r:'Minor artefacts',      s:95, current:true},
            {l:'Visible',   r:'Notable artefacts',    s:55},
            {l:'Severe',    r:'Dominant artefacts',   s:20}
           ],
           sources:['Orthophoto','Reconstruction report'],
           rec:'Minor artefacts only; cleared for client delivery.', alert:null},
          {name:'Geometric Accuracy',
           desc:'How well horizontal positions on the orthomosaic match the independent check points.',
           grades:[
            {l:'Professional',r:'Under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Reject',      r:'10 cm or more',s:20}
           ],
           sources:['Check point file','Accuracy report'],
           rec:'Horizontal accuracy meets professional grade.', alert:null}
         ]},

        {id:'mdl', name:'3D Model',
         fmt:'OBJ + MTL + JPG textures', sz:'2.2 GB (textured mesh)',
         score:82,
         travelsTo:'3D model is delivered to the client for visualisation. Does not feed the Analytics universe.',
         desc:'A textured three-dimensional mesh suitable for client visualisation. Generated only when explicitly requested in the deliverable set.',
         tierAuth:{
           Gold:'Approved for client visualisation and stakeholder presentation.',
           Silver:'Approved for client visualisation. Minor mesh limitations documented.',
           Bronze:'Acceptable for internal visualisation and review.',
           Marginal:'Mesh is incomplete or distorted. Use with care; consider regeneration.',
           Poor:'Mesh is unfit for delivery.'
         },
         checks:[
          {name:'Mesh Completeness',
           desc:'Share of the surveyed area represented in the mesh.',
           grades:[
            {l:'Complete',  r:'98% or more',  s:100},
            {l:'Strong',    r:'95% or more',  s:82, current:true},
            {l:'Partial',   r:'less than 90%',s:40, flag:'Mesh coverage incomplete'}
           ],
           sources:['3D model file'],
           rec:'Coverage is strong; Bench 3 area carries minor mesh gaps.',
           alert:null},
          {name:'Texture Resolution',
           desc:'Resolution of the texture atlas applied to the mesh.',
           grades:[
            {l:'16K or higher',r:'Highest resolution', s:100},
            {l:'8K',           r:'High resolution',    s:75, current:true},
            {l:'4K',           r:'Standard resolution',s:55},
            {l:'Low',          r:'Below 4K',           s:25}
           ],
           sources:['3D model file'],
           rec:'8K textures suit the area scale.', alert:null},
          {name:'File Integrity',
           desc:'Whether the model file parses cleanly without errors.',
           grades:[
            {l:'Clean parse',r:'No errors',       s:100, current:true},
            {l:'Parse error',r:'File rejected',   s:0,   flag:'Model file corrupt'}
           ],
           sources:['3D model file'],
           rec:'Model file is intact.', alert:null}
         ]},

        {id:'rpt', name:'Accuracy Report',
         fmt:'PDF + JSON', sz:'820 KB',
         score:94,
         travelsTo:'Accuracy report is delivered to the client and to regulatory reviewers. The most authoritative single deliverable in the survey.',
         desc:'The independent verification of survey accuracy, based on the check points held back from reconstruction. This is the only deliverable backed by independent evidence.',
         tierAuth:{
           Gold:'Approved for all mining applications including volume work, regulatory compliance, engineering design, stockpile auditing and legal boundary work.',
           Silver:'Approved for most mining applications. Volume work is reliable. Check regulatory specifications.',
           Bronze:'Acceptable for progress monitoring and overview. Volume work carries ten percent uncertainty. Not for regulatory compliance or precision engineering.',
           Marginal:'Multiple quality dimensions underperforming. Volume work carries large uncertainty. Inform the client before business decisions.',
           Poor:'Independent verification failed. No analytics until root cause is resolved.'
         },
         checks:[
          {name:'Horizontal Accuracy',
           desc:'Root-mean-square horizontal error of the reconstruction against the independent check points.',
           grades:[
            {l:'Professional',r:'Under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Reject',      r:'10 cm or more',s:20}
           ],
           sources:['Check point file','Accuracy report'],
           rec:'Horizontal accuracy meets professional grade.', alert:null},
          {name:'Vertical Accuracy',
           desc:'Root-mean-square vertical error of the reconstruction against the independent check points.',
           grades:[
            {l:'Professional',r:'Under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Reject',      r:'10 cm or more',s:20}
           ],
           sources:['Check point file','Accuracy report'],
           rec:'Vertical accuracy meets professional grade.', alert:null},
          {name:'Check Point Count',
           desc:'Number of check points used in the verification.',
           grades:[
            {l:'Robust',  r:'10 or more', s:100},
            {l:'Adequate',r:'5 to 9',     s:88, current:true},
            {l:'Sparse',  r:'3 to 4',     s:55},
            {l:'None',    r:'1 or fewer', s:0,  flag:'No independent verification'}
           ],
           sources:['Check point file'],
           rec:'Six check points support strong statistical validation.', alert:null},
          {name:'Consistency',
           desc:'Whether check point residuals are evenly distributed without systematic bias or outliers.',
           grades:[
            {l:'Even',     r:'Within 2 sigma',     s:100, current:true},
            {l:'Acceptable',r:'One mild outlier',  s:75},
            {l:'Biased',   r:'Systematic offset',  s:40, flag:'Check point bias'},
            {l:'Scattered',r:'Multiple outliers',  s:25, flag:'Multiple outliers'}
           ],
           sources:['Accuracy report'],
           rec:'Residuals are evenly distributed.', alert:null},
          {name:'Report Completeness',
           desc:'Whether all expected fields in the accuracy report are present.',
           grades:[
            {l:'Complete',  r:'Every field present',s:100, current:true},
            {l:'Acceptable',r:'One field missing',  s:75},
            {l:'Incomplete',r:'Two or more missing',s:30, flag:'Report incomplete'}
           ],
           sources:['Accuracy report'],
           rec:'Report is complete.', alert:null}
         ]}
      ]
    },

    {
      id:'ana-del', name:'Analytics Deliverables', short:'Analytics',
      col:'#00B4D8',
      desc:'The final measurement results delivered to the client.',
      items: [

        {id:'stk', name:'Stockpile Volume',
         fmt:'PDF + XLSX', sz:'1.2 MB (5 stockpiles)',
         score:92,
         travelsTo:'Stockpile volume report is delivered to the client for monthly reconciliation.',
         desc:'Per-stockpile volume results with uncertainty bounds, computed from the bare-earth surface and the stockpile boundary polygons.',
         tierAuth:{
           Gold:'Approved for all commercial decisions including reconciliation, audit and regulatory reporting.',
           Silver:'Approved for most commercial decisions. Volume uncertainty disclosed.',
           Bronze:'Acceptable for progress monitoring and trend analysis. Disclose uncertainty in any commercial use.',
           Marginal:'Significant concerns. Quantitative use requires explicit client acknowledgment.',
           Poor:'Stockpile result is unfit for delivery.'
         },
         checks:[
          {name:'Source Surface Quality',
           desc:'How accurate the bare-earth surface is in the stockpile area.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Accuracy report'],
           rec:'Source surface supports professional-grade volume.', alert:null},
          {name:'Boundary Quality',
           desc:'How cleanly the AI detected each stockpile boundary.',
           grades:[
            {l:'Clean',     r:'All boundaries sharp',s:100, current:true},
            {l:'Acceptable',r:'Minor noise',         s:85},
            {l:'Loose',     r:'Notable noise',       s:55},
            {l:'Poor',      r:'Boundary unclear',    s:20}
           ],
           sources:['Stockpile report'],
           rec:'All five stockpile boundaries are clean.', alert:null},
          {name:'Volume Method',
           desc:'The algorithm used to compute volume against a base surface.',
           grades:[
            {l:'Triangulated base', r:'TIN method',  s:100, current:true},
            {l:'Plane base',        r:'Lowest-elevation method',s:80},
            {l:'Average base',      r:'Mean-plane method',      s:60}
           ],
           sources:['Stockpile report'],
           rec:'TIN method applied.', alert:null},
          {name:'Coverage Adequacy',
           desc:'Share of each stockpile footprint covered by valid surface measurements.',
           grades:[
            {l:'Complete', r:'95% or more',s:100, current:true},
            {l:'Partial',  r:'80% or more',s:65},
            {l:'Patchy',   r:'less than 80%',s:25, flag:'Coverage thin'}
           ],
           sources:['Bare-earth surface raster','Stockpile report'],
           rec:'Every stockpile footprint is fully covered.', alert:null},
          {name:'Result Plausibility',
           desc:'Whether the computed volumes sit within expected ranges given the site context.',
           grades:[
            {l:'Plausible', r:'Within expected range',s:100, current:true},
            {l:'Notable',   r:'Mild deviation',       s:75},
            {l:'Suspect',   r:'Notable deviation',    s:50, flag:'Result deviation'},
            {l:'Implausible',r:'Strong deviation',    s:20}
           ],
           sources:['Stockpile report','Reference survey'],
           rec:'Results sit within expected ranges.', alert:null}
         ]},

        {id:'pit', name:'Pit Volume',
         fmt:'PDF + DXF', sz:'2.8 MB',
         score:88,
         travelsTo:'Pit volume report is delivered to the client for monthly compliance submission.',
         desc:'Pit volume, depth profile and bench area results. Pit walls are handled specially because steep walls leave expected gaps in the bare-earth surface.',
         tierAuth:{
           Gold:'Approved for all commercial decisions including audit and regulatory reporting.',
           Silver:'Approved for most commercial decisions. Volume uncertainty disclosed.',
           Bronze:'Acceptable for progress monitoring. Disclose uncertainty in any commercial use.',
           Marginal:'Significant concerns. Quantitative use requires explicit client acknowledgment.',
           Poor:'Pit result is unfit for delivery.'
         },
         checks:[
          {name:'Source Surface Quality',
           desc:'How accurate the bare-earth surface is across the pit area.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Accuracy report'],
           rec:'Source surface supports professional-grade pit volume.', alert:null},
          {name:'Boundary Quality',
           desc:'How cleanly the AI detected the pit boundary.',
           grades:[
            {l:'Clean',     r:'Sharp boundary',  s:100},
            {l:'Acceptable',r:'Minor noise',     s:88, current:true},
            {l:'Loose',     r:'Notable noise',   s:55},
            {l:'Poor',      r:'Boundary unclear',s:20}
           ],
           sources:['Pit report'],
           rec:'Boundary is clean with minor edge noise.', alert:null},
          {name:'Wall Coverage',
           desc:'How well the pit walls were captured by the survey. Steep walls leave expected gaps.',
           grades:[
            {l:'Strong',   r:'Walls well captured',s:100},
            {l:'Acceptable',r:'Most walls captured',s:85, current:true},
            {l:'Weak',     r:'Walls under-served', s:50, flag:'Wall coverage thin'}
           ],
           sources:['Point cloud file','Pit report'],
           rec:'Walls are well captured.', alert:null},
          {name:'Volume Method',
           desc:'The algorithm used to compute the pit volume.',
           grades:[
            {l:'Triangulated lid', r:'TIN method',s:100, current:true},
            {l:'Plane lid',        r:'Lowest-elevation method',s:80}
           ],
           sources:['Pit report'],
           rec:'TIN method applied.', alert:null},
          {name:'Result Plausibility',
           desc:'Whether the computed volume sits within expected ranges given the site context.',
           grades:[
            {l:'Plausible',r:'Within expected range', s:100, current:true},
            {l:'Notable',  r:'Mild deviation',        s:75},
            {l:'Suspect',  r:'Notable deviation',     s:50}
           ],
           sources:['Pit report'],
           rec:'Result is plausible.', alert:null}
         ]},

        {id:'wsd', name:'Waste Dump Volume',
         fmt:'PDF + XLSX', sz:'1.6 MB',
         score:78,
         travelsTo:'Waste dump report is delivered to the client and used in regulatory compliance.',
         desc:'Waste dump volume and surface area results, computed against the bare-earth surface and a reference baseline.',
         tierAuth:{
           Gold:'Approved for compliance reporting and audit.',
           Silver:'Approved for compliance reporting with disclosed limitations.',
           Bronze:'Acceptable for progress monitoring. Compliance submission requires caveats.',
           Marginal:'Significant concerns. Validate before submission.',
           Poor:'Waste dump result is unfit for delivery.'
         },
         checks:[
          {name:'Source Surface Quality',
           desc:'How accurate the bare-earth surface is across the waste dump area.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Accuracy report'],
           rec:'Surface supports survey-grade results.', alert:null},
          {name:'Boundary Quality',
           desc:'How cleanly the AI detected each waste dump boundary.',
           grades:[
            {l:'Clean',     r:'Sharp boundary',  s:100},
            {l:'Acceptable',r:'Minor noise',     s:85},
            {l:'Provisional',r:'Notable noise',  s:55, current:true, flag:'Boundary provisional'},
            {l:'Poor',      r:'Boundary unclear',s:20}
           ],
           sources:['Waste dump report'],
           rec:'Validate Dump 2 boundary before submission.',
           alert:'Dump 2 boundary depends on the Bench 3 mesh quality.'},
          {name:'Reference Surface Quality',
           desc:'How accurate the reference (baseline) surface is for comparison.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100},
            {l:'Engineering', r:'Under 5 cm',s:85, current:true},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Reference survey','Volume report (Waste Dump)'],
           rec:'Reference surface is engineering-grade.', alert:null},
          {name:'Coverage Adequacy',
           desc:'Share of each waste dump footprint covered by valid surface measurements.',
           grades:[
            {l:'Complete',r:'95% or more',s:100, current:true},
            {l:'Partial', r:'80% or more',s:65},
            {l:'Patchy',  r:'less than 80%',s:25}
           ],
           sources:['Bare-earth surface raster','Waste dump report'],
           rec:'Coverage is complete.', alert:null},
          {name:'Result Plausibility',
           desc:'Whether the computed volumes sit within expected ranges.',
           grades:[
            {l:'Plausible',r:'Within expected range',s:100, current:true},
            {l:'Notable',  r:'Mild deviation',       s:75},
            {l:'Suspect',  r:'Notable deviation',    s:50}
           ],
           sources:['Waste dump report'],
           rec:'Results sit within expected ranges.', alert:null}
         ]},

        {id:'cf', name:'Cut and Fill',
         fmt:'PDF + GeoTIFF', sz:'1.9 MB',
         score:42,
         travelsTo:'Cut and fill report is delivered to the client when comparing two surveys or a survey against a design.',
         desc:'Volumes of material added (fill) and removed (cut) between two surfaces. Always requires a reference surface — either a previous survey or a design file.',
         tierAuth:{
           Gold:'Approved for all commercial decisions including reconciliation and engineering design.',
           Silver:'Approved for most commercial decisions with disclosed uncertainty.',
           Bronze:'Acceptable for progress monitoring. Disclose uncertainty in any commercial use.',
           Marginal:'Significant concerns. Quantitative use requires explicit client acknowledgment.',
           Poor:'Cut and fill result is unfit for certification.'
         },
         checks:[
          {name:'Current Surface Quality',
           desc:'How accurate the current bare-earth surface is across the comparison area.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100, current:true},
            {l:'Engineering', r:'Under 5 cm',s:85},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Accuracy report'],
           rec:'Current surface is survey-grade.', alert:null},
          {name:'Reference Surface Quality',
           desc:'How accurate the reference (baseline) surface is.',
           grades:[
            {l:'Survey-grade',r:'Vertical accuracy under 3 cm',s:100},
            {l:'Engineering', r:'Under 5 cm',s:85, current:true},
            {l:'Mapping',     r:'Under 10 cm',s:60},
            {l:'Poor',        r:'10 cm or more',s:25}
           ],
           sources:['Reference survey','Cut and fill report'],
           rec:'Reference is engineering-grade.', alert:null},
          {name:'Combined Uncertainty',
           desc:'How much measurement noise the two surfaces together leave in the result.',
           grades:[
            {l:'Tight',   r:'Differences well above noise',s:100},
            {l:'Workable',r:'Differences near noise floor',s:75},
            {l:'Wide',    r:'Differences within noise',    s:25, current:true, flag:'Statistical anomaly'}
           ],
           sources:['Cut and fill report'],
           rec:'Re-fly Bench 3 before certifying cut and fill.',
           alert:'Bench 3 shows a 3.8 sigma deviation from expected change.'},
          {name:'Spatial Alignment',
           desc:'How well the two surfaces line up in space (same grid, same resolution).',
           grades:[
            {l:'Aligned',  r:'Same resolution and grid',s:100, current:true},
            {l:'Close',    r:'Within 2x of each other',  s:80},
            {l:'Misaligned',r:'Over 2x apart',           s:40}
           ],
           sources:['Reference survey'],
           rec:'Surfaces align cleanly.', alert:null},
          {name:'Result Plausibility',
           desc:'Whether the computed volumes sit within expected ranges given the site context.',
           grades:[
            {l:'Plausible',r:'Within expected range',s:100},
            {l:'Notable',  r:'Mild deviation',       s:75},
            {l:'Suspect',  r:'Notable deviation',    s:55, current:true},
            {l:'Implausible',r:'Strong deviation',   s:20}
           ],
           sources:['Cut and fill report'],
           rec:'Net volume is below expected; tied to the Bench 3 anomaly.', alert:null}
         ]},

        {id:'ter', name:'Terrain Map',
         fmt:'GeoTIFF + Shapefile', sz:'320 MB',
         score:88,
         travelsTo:'Terrain map travels to the client as a derived product set: slope, aspect, hillshade and contours.',
         desc:'Terrain derivative products generated from the bare-earth surface: slope, aspect, hillshade, and contour lines.',
         tierAuth:{
           Gold:'Approved for engineering planning, regulatory submission and stakeholder presentation.',
           Silver:'Approved for engineering planning. Minor accuracy limitations documented.',
           Bronze:'Acceptable for mapping-grade reference and visual context.',
           Marginal:'Visual reference only. Contour intervals carry significant uncertainty.',
           Poor:'Terrain map is unfit for delivery.'
         },
         checks:[
          {name:'Source Surface Quality',
           desc:'How accurate the bare-earth surface used to derive the terrain products is.',
           grades:[
            {l:'High',  r:'Vertical accuracy under 3 cm',s:100},
            {l:'Strong',r:'Under 5 cm',s:93, current:true},
            {l:'Mapping',r:'Under 10 cm',s:60},
            {l:'Poor',  r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Accuracy report'],
           rec:'Source surface supports professional terrain products.', alert:null},
          {name:'Resolution Adequacy',
           desc:'Whether the source resolution suits the requested terrain product type.',
           grades:[
            {l:'High',      r:'5 cm or less', s:100},
            {l:'Survey',    r:'10 cm or less',s:88, current:true},
            {l:'Mapping',   r:'25 cm or less',s:60},
            {l:'Insufficient',r:'Over 25 cm', s:25}
           ],
           sources:['Bare-earth surface raster metadata'],
           rec:'Resolution suits the derivative products.', alert:null},
          {name:'Coverage Completeness',
           desc:'Share of the AOI covered by valid derivative values.',
           grades:[
            {l:'Complete',r:'99% or more',s:100, current:true},
            {l:'Partial', r:'95% or more',s:75},
            {l:'Sparse',  r:'less than 95%',s:35}
           ],
           sources:['Terrain analysis output'],
           rec:'Full coverage of the AOI.', alert:null}
         ]},

        {id:'cmp', name:'Surface Comparison',
         fmt:'PDF + GeoTIFF', sz:'780 KB',
         score:80,
         travelsTo:'Surface comparison report is delivered to the client when comparing two surfaces (current and reference).',
         desc:'Quantitative or visual comparison of two surfaces. Quantitative mode produces an elevation difference raster with stated noise floor; visual mode produces an overlay only.',
         tierAuth:{
           Gold:'Approved for engineering decisions and regulatory reporting.',
           Silver:'Approved for engineering decisions. Minor limitations documented.',
           Bronze:'Acceptable for progress monitoring and trend analysis.',
           Marginal:'Significant concerns. Quantitative use requires client acknowledgment.',
           Poor:'Comparison result is unfit for delivery.'
         },
         checks:[
          {name:'Current Surface Quality',
           desc:'Quality of the first surface in the comparison.',
           grades:[
            {l:'High',  r:'Vertical accuracy under 3 cm',s:100},
            {l:'Strong',r:'Under 5 cm',s:93, current:true},
            {l:'Mapping',r:'Under 10 cm',s:60},
            {l:'Poor',  r:'10 cm or more',s:25}
           ],
           sources:['Bare-earth surface raster','Surface model raster'],
           rec:'Current surface is publication-grade.', alert:null},
          {name:'Reference Surface Quality',
           desc:'Quality of the second surface in the comparison.',
           grades:[
            {l:'High',  r:'Vertical accuracy under 3 cm',s:100},
            {l:'Strong',r:'Under 5 cm',s:93},
            {l:'Mapping',r:'Under 10 cm',s:60, current:true},
            {l:'Poor',  r:'10 cm or more',s:25}
           ],
           sources:['Reference survey'],
           rec:'Reference is mapping-grade; comparison precision is bounded by this.', alert:null},
          {name:'Temporal Alignment',
           desc:'How well the two surfaces represent comparable points in time.',
           grades:[
            {l:'Aligned',  r:'Same week',     s:100},
            {l:'Acceptable',r:'Within 30 days',s:75, current:true},
            {l:'Loose',    r:'Within 90 days',s:50},
            {l:'Mismatched',r:'Over 90 days', s:25}
           ],
           sources:['Reference survey'],
           rec:'Temporal alignment is acceptable.', alert:null},
          {name:'Resolution Compatibility',
           desc:'Whether the two surfaces share comparable resolution.',
           grades:[
            {l:'Matched',  r:'Same resolution',s:100, current:true},
            {l:'Close',    r:'Within 2x',     s:80},
            {l:'Mismatched',r:'Over 2x apart',s:40}
           ],
           sources:['Reference survey'],
           rec:'Resolutions match.', alert:null},
          {name:'Interpretability',
           desc:'Whether the comparison supports quantitative or only visual interpretation.',
           grades:[
            {l:'Quantitative',r:'Numeric differences valid',s:100, current:true},
            {l:'Visual',      r:'Visual only',                s:60}
           ],
           sources:['Surface comparison report'],
           rec:'Results are quantitatively interpretable.', alert:null}
         ]}
      ]
    }
  ]
};


// Derive the table-shape DELIVERABLES list from the ontology so the table
// and the right-side panel share one source of truth.
function recBucketForTier(tierName){
  if(tierName==='Gold' || tierName==='Silver') return 'go';
  if(tierName==='Bronze' || tierName==='Marginal') return 'partial';
  return 'no';
}
function recShortForTier(tierName){
  return {Gold:'Good to Go', Silver:'Good to Go', Bronze:'Mapping Grade Only', Marginal:'Review Recommended', Poor:'No Go'}[tierName] || tierName;
}

var DELIVERABLES = DELIVERABLE_ONTOLOGY.groups.map(function(grp){
  return {
    group: grp.short, // 'Capture' / 'Processing' / 'Analytics' for table headers
    items: grp.items.map(function(d){
      var tier = tierFor(d.score);
      return {
        n: d.name,
        fmt: d.fmt,
        sz: d.sz,
        score: d.score,
        rec: recBucketForTier(tier.name),
        recShort: recShortForTier(tier.name),
        recLong: d.tierAuth[tier.name] || '',
        ontologyRef: d
      };
    })
  };
});

// per-deliverable selected status, keyed by group:index
var DEL_STATUS={};

function recPillClass(rec){
  return rec==='go'?'go':rec==='no'?'no':rec==='partial'?'partial':rec==='mapping'?'mapping':'partial';
}

// Map a recommendation bucket to its mockup verdict styling.
// Goes: bright green dot/label/score
// Partial: gold dot/label/score
// No: oxide red dot/label/score
function verdictClasses(rec){
  if(rec==='go')      return {row:'ds-cert', dot:'dd-cert', lbl:'dv-cert', label:'Certified'};
  if(rec==='partial') return {row:'ds-rev',  dot:'dd-rev',  lbl:'dv-rev',  label:'Review'};
  return                    {row:'ds-nogo', dot:'dd-nogo', lbl:'dv-nogo', label:'No-Go'};
}

// Build the deliverables view per preview-17.html structure:
//   - manifest header (eyebrow + Raleway-200 site title + sub-line + status pill cluster)
//   - per-group divider
//   - flat row: identity | verdict+score | hover-revealed dropdown | hover-revealed download
function buildDelView(){
  var cont = document.getElementById('view-del');

  // Count by verdict bucket
  var goC=0, warnC=0, noC=0;
  DELIVERABLES.forEach(function(grp){
    grp.items.forEach(function(d){
      if(d.rec==='go') goC++;
      else if(d.rec==='no') noC++;
      else warnC++;
    });
  });
  var total = goC + warnC + noC;

  // Site / date / location come from ONTOLOGY where defined
  var site = (typeof ONTOLOGY!=='undefined' && ONTOLOGY.site) ? ONTOLOGY.site : 'Pitpack 4';
  var date = (typeof ONTOLOGY!=='undefined' && ONTOLOGY.instance) ? ONTOLOGY.instance : '28 Mar 2026';
  var location = (typeof ONTOLOGY!=='undefined' && ONTOLOGY.location) ? ONTOLOGY.location : 'Jharkhand';

  var h = '<div class="del-head">'
        +   '<div class="del-head-l">'
        +     '<div class="del-eyebrow">Artefact Manifest</div>'
        +     '<div class="del-site">'+site+'</div>'
        +     '<div class="del-sub">'+date+' &nbsp;&middot;&nbsp; '+total+' artefact'+(total===1?'':'s')+' &nbsp;&middot;&nbsp; '+location+'</div>'
        +   '</div>'
        +   '<div class="del-head-r">'
        +     '<div class="dsp dsp-go">'+goC+' Certified</div>'
        +     '<div class="dsp dsp-hold">'+warnC+' Review</div>'
        +     '<div class="dsp dsp-no">'+noC+' No-Go</div>'
        +   '</div>'
        + '</div>';

  DELIVERABLES.forEach(function(grp, grpIdx){
    h += '<div class="del-gate">'
       +   '<span class="del-gate-name">'+grp.group+'</span>'
       +   '<span class="del-gate-ct">'+grp.items.length+'</span>'
       + '</div>';

    grp.items.forEach(function(d, idx){
      var key = grp.group + ':' + idx;
      var sel = DEL_STATUS[key] || '';
      var v = verdictClasses(d.rec);
      var did = 'dr-' + grpIdx + '-' + idx;

      // Pre-applied decision class on the dropdown
      var ddCls = sel==='accept' ? 'dd-accept'
                : sel==='defer'  ? 'dd-defer'
                : sel==='reject' ? 'dd-reject'
                : '';

      h += '<div class="del-row '+v.row+'">'

         // COL A — identity (clickable opens detail panel)
         + '<div class="da-id" onclick="openDeliverable('+grpIdx+','+idx+')">'
         +   '<div class="da-name">'+d.n+'</div>'
         +   '<div class="da-meta">'
         +     '<span class="da-tag">'+d.fmt+'</span>'
         +     '<span class="da-sep"></span>'
         +     '<span class="da-sz">'+d.sz+'</span>'
         +     (d.recLong ? '<span class="da-sep"></span><span class="da-toggle" onclick="event.stopPropagation(); toggleRationale(\''+did+'\',this)">Rationale &rsaquo;</span>' : '')
         +   '</div>'
         +   (d.recLong ? '<div class="da-rationale" id="'+did+'">'+d.recLong+'</div>' : '')
         + '</div>'

         // COL B — verdict + score
         + '<div class="da-verdict '+v.row+'">'
         +   '<div class="da-v-badge">'
         +     '<span class="da-dot '+v.dot+'"></span>'
         +     '<span class="da-v-label '+v.lbl+'">'+v.label+'</span>'
         +   '</div>'
         +   '<span class="da-vsep">&middot;</span>'
         +   '<div class="da-score-n">'+d.score+'<span class="da-score-pct">%</span></div>'
         + '</div>'

         // COL C — decision dropdown (hover-revealed unless decided)
         + '<div class="da-action">'
         +   '<select class="da-dd '+ddCls+'" data-key="'+key+'" onchange="setDecision(this)">'
         +     '<option value="" disabled '+(sel===''?'selected':'')+'>&mdash; Decision</option>'
         +     '<option value="accept" '+(sel==='accept'?'selected':'')+'>Accept</option>'
         +     '<option value="defer"  '+(sel==='defer' ?'selected':'')+'>Defer</option>'
         +     '<option value="reject" '+(sel==='reject'?'selected':'')+'>Reject</option>'
         +   '</select>'
         + '</div>'

         // COL D — download (hover-revealed)
         + '<div class="da-dl">'
         +   '<button class="da-dl-btn" title="Download" data-name="'+d.n.replace(/"/g,'')+'" onclick="downloadDel(this.dataset.name)">'
         +     '<svg viewBox="0 0 14 14" fill="none"><path d="M7 2v8M3.5 7L7 10.5 10.5 7M2 12h10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
         +   '</button>'
         + '</div>'

         + '</div>';
    });
  });

  cont.innerHTML = h;
}

// Toggle the rationale paragraph below an artefact row.
function toggleRationale(id, btn){
  var el = document.getElementById(id);
  if(!el) return;
  var open = el.classList.toggle('open');
  btn.innerHTML = open ? 'Hide &lsaquo;' : 'Rationale &rsaquo;';
}

// Persist the user's decision and update the dropdown's visual state.
function setDecision(sel){
  var key = sel.dataset.key;
  var val = sel.value;
  DEL_STATUS[key] = val;
  sel.classList.remove('dd-accept','dd-defer','dd-reject');
  if(val) sel.classList.add('dd-'+val);
}

function downloadDel(name){
  // placeholder -- in production this calls the file API
  console.log('Download requested:', name);
}

// ============================================================
// SITE REALITY VIEW
// ============================================================
var LAYERS=[
  {group:'Capture', items:[
    {id:'flight',  name:'Flight Path',    on:false, type:'path'},
    {id:'drone',   name:'Drone',          on:false, type:'marker'},
    {id:'base',    name:'Base Station',   on:false, type:'marker'},
    {id:'gcps',    name:'Control Points',           on:false, type:'points'}
  ]},
  {group:'Processing', items:[
    {id:'images',  name:'Geotagged Images', on:false, type:'points'},
    {id:'ortho',   name:'Orthomosaic',      on:true,  type:'raster', heavy:true},
    {id:'dsm',     name:'DSM',              on:false, type:'raster', heavy:true},
    {id:'dtm',     name:'DTM',              on:false, type:'raster', heavy:true},
    {id:'mesh',    name:'3D Model',         on:false, type:'mesh',   heavy:true},
    {id:'pcd',     name:'Point Cloud',      on:false, type:'pointcloud', heavy:true}
  ]},
  {group:'Analytics', items:[
    {id:'stockpiles', name:'Stockpiles',  on:true,  type:'polygon', multi:true},
    {id:'pits',       name:'Pits',        on:false, type:'polygon', multi:true},
    {id:'dumps',      name:'Waste Dumps', on:false, type:'polygon', multi:true},
    {id:'cutfill',    name:'Cut-Fill',    on:false, type:'polygon', heavy:true}
  ]}
];

// AOI bounding box in canvas coords -- normalised, scaled at render time
// Features are defined in 0..1 space then mapped to canvas
// Each feature carries a `score` (0-100) and optional `anomaly` (string description)
var FEATURES={
  flight:[ // flight path waypoints, normalised
    [.15,.18],[.35,.16],[.55,.18],[.78,.22],[.82,.42],[.78,.62],[.55,.74],[.32,.78],[.18,.62],[.16,.42],[.20,.24]
  ],
  drone:{x:.55,y:.18,heading:90,score:96}, // current drone position
  base:{x:.10,y:.86,name:'Base Station',meta:'Trimble R12i, RTK Fix',score:98},
  gcps:[
    {x:.22,y:.28,id:'Control Point-1',res:8, score:94},
    {x:.62,y:.24,id:'Control Point-2',res:14,score:62, anomaly:'Residual exceeds 10mm survey threshold'},
    {x:.78,y:.46,id:'Control Point-3',res:9, score:92},
    {x:.74,y:.70,id:'Control Point-4',res:11,score:78, anomaly:'Marginal: 1mm above survey threshold'},
    {x:.42,y:.72,id:'Control Point-5',res:7, score:96},
    {x:.26,y:.54,id:'Control Point-6',res:10,score:90}
  ],
  images:(function(){var a=[];for(var i=0;i<60;i++)a.push({x:.15+Math.random()*.68,y:.18+Math.random()*.62});return a;})(),
  stockpiles:[
    {id:'SP-A',name:'Stockpile A',pts:[[.32,.36],[.40,.34],[.43,.40],[.38,.44],[.32,.42]],vol:178,grade:'A',score:94},
    {id:'SP-B',name:'Stockpile B',pts:[[.50,.50],[.58,.48],[.60,.55],[.54,.58],[.49,.55]],vol:142,grade:'A',score:91},
    {id:'SP-C',name:'Stockpile C',pts:[[.66,.38],[.72,.37],[.74,.43],[.70,.46],[.65,.43]],vol:94, grade:'B',score:74,anomaly:'Volume drift of 12% vs. 27 Feb baseline'},
    {id:'SP-D',name:'Stockpile D',pts:[[.36,.60],[.44,.59],[.46,.65],[.40,.68],[.34,.66]],vol:121,grade:'A',score:90},
    {id:'SP-E',name:'Stockpile E',pts:[[.56,.66],[.62,.65],[.63,.71],[.58,.73],[.55,.70]],vol:49, grade:'C',score:68,anomaly:'AI classification confidence below 80%'}
  ],
  pits:[
    {id:'PIT-1',name:'North Pit',pts:[[.28,.22],[.46,.20],[.50,.30],[.44,.36],[.30,.34]],depth:18.4,score:89},
    {id:'PIT-2',name:'South Pit',pts:[[.46,.56],[.66,.54],[.70,.66],[.64,.74],[.48,.72]],depth:24.7,score:84,anomaly:'Slope angle 47\u00B0 exceeds safe threshold (42\u00B0)'}
  ],
  dumps:[
    {id:'WD-1',name:'Dump 1',pts:[[.12,.42],[.22,.40],[.24,.48],[.18,.52],[.12,.50]],vol:412,score:88},
    {id:'WD-2',name:'Dump 2',pts:[[.78,.30],[.86,.30],[.86,.40],[.80,.42]],vol:289,flag:'provisional',score:64,anomaly:'Boundary provisional; depends on Bench 3 mesh'}
  ],
  cutfill:[
    {id:'CF-B3',name:'Bench 3',pts:[[.58,.30],[.70,.28],[.72,.38],[.64,.42],[.58,.38]],delta:-3.8,sigma:true,score:42,anomaly:'-3.8 sigma deviation; statistically significant'}
  ]
};

// per-layer insights -- KPIs, recommendations, anomalies
var LAYER_INSIGHTS={
  flight:    {score:88, kpis:[['Waypoints','248'],['Coverage','94%'],['Duration','22 min'],['Altitude','85 m AGL']], recs:['Overlap at 87% (target 80%); cleared for reconstruction.'], anom:[], alerts:[]},
  drone:     {score:92, kpis:[['Battery','62%'],['Speed','7.2 m/s'],['Pitch','-12 deg'],['Telemetry','RTK Fix']], recs:[], anom:[], alerts:['Drone is mid-mission; live feed enabled.']},
  base:      {score:96, kpis:[['Status','RTK Fix'],['Satellites','18'],['HRMS','0.008 m'],['VRMS','0.014 m']], recs:['Solution quality acceptable for survey-grade work.'], anom:[], alerts:[]},
  gcps:      {score:78, kpis:[['Control Points','6'],['Mean Resid.','9.8 mm'],['Worst','Control Point-2 (14 mm)'],['NE Coverage','Weak']], recs:['Relocate Control Point-2 or add 2 Control Points in NE quadrant to certify survey-grade.'], anom:['Control Point-2 residual exceeds 10 mm survey threshold.'], alerts:[]},
  images:    {score:94, kpis:[['Frames','2,841'],['Geotagged','100%'],['GSD','4.8 cm'],['Sun Angle','42 deg']], recs:['Imagery ready for photogrammetric processing.'], anom:[], alerts:[]},
  ortho:     {score:95, kpis:[['Resolution','4.8 cm/px'],['Coverage','100%'],['Seamlines','2 masked'],['Size','4.1 GB']], recs:['Cleared for client delivery.'], anom:[], alerts:[]},
  dsm:       {score:93, kpis:[['Grid','10 cm'],['Vert. RMSE','+/-2.1 cm'],['Horz. RMSE','+/-1.8 cm'],['Coverage','100%']], recs:['Meets ASPRS Professional Grade.'], anom:[], alerts:[]},
  dtm:       {score:71, kpis:[['Grid','10 cm'],['Class. Conf.','85%'],['Ground %','62%'],['Vert. RMSE','+/-2.6 cm']], recs:['Bench 3 vegetation filter leaves residual canopy artefacts.'], anom:['Bench 3 ground classification confidence at 71%.'], alerts:[]},
  mesh:      {score:76, kpis:[['Triangles','42 M'],['Texture','8K'],['Bench 3 Tie Q','0.61 px'],['Format','OBJ + MTL']], recs:['Re-tie Bench 3 before engineering use.'], anom:[], alerts:[]},
  pcd:       {score:96, kpis:[['Points','148 M'],['Density','42 pts/m^2'],['Voids','0'],['Classification','Complete']], recs:['Ready for engineering-grade downstream use.'], anom:[], alerts:[]},
  stockpiles:{score:92, kpis:[['Count','5'],['Total Vol.','584 m^3'],['Method','TIN'],['AI Conf.','92%']], recs:['All 5 stockpiles detected; volumes within +/-3%.'], anom:[], alerts:[]},
  pits:      {score:86, kpis:[['Count','2'],['Max Depth','24.7 m'],['Plan Dev.','+1.8%'],['Compliance','Pass']], recs:['Compliance to plan within tolerance; ready for submission.'], anom:[], alerts:[]},
  dumps:     {score:72, kpis:[['Count','2'],['Total Vol.','701 m^3'],['Provisional','Dump 2'],['Status','Review']], recs:['Validate Dump 2 boundary before submission.'], anom:['Dump 2 boundary provisional due to Bench 3 uncertainty.'], alerts:[]},
  cutfill:   {score:42, kpis:[['Net Volume','-215 m^3'],['Cut','312 m^3'],['Fill','97 m^3'],['Sigma','-3.8']], recs:['Re-fly Bench 3 before certifying cut-fill against last survey.'], anom:['Bench 3 change detection at -3.8 sigma; computation unreliable.'], alerts:['Anomaly flagged: investigate before delivery.']}
};

// per-feature detail (used when an object is clicked on the map)
function featureDetail(layerId,feat){
  // helper: pull seeded anomaly into the detail
  var anomArr=(feat && feat.anomaly)?[feat.anomaly]:[];
  var scoreKpi=(feat && typeof feat.score==='number')?[['Score',feat.score+' / 100']]:[];

  if(layerId==='gcps'){
    return {tag:'Control Point',name:feat.id,meta:'Residual '+feat.res+' mm',
      kpis:scoreKpi.concat([['Residual',feat.res+' mm'],['Status',feat.res>10?'Above Threshold':'Pass'],['Coords','23.71'+(feat.id.slice(-1))+'N']]),
      recs:[feat.res>10?'Above 10 mm survey threshold. Relocate or remeasure.':'Within survey-grade tolerance.'],
      anom:anomArr,alerts:[]};
  }
  if(layerId==='base'){
    return {tag:'Base Station',name:feat.name,meta:feat.meta,
      kpis:scoreKpi.concat(LAYER_INSIGHTS.base.kpis),recs:LAYER_INSIGHTS.base.recs,anom:[],alerts:[]};
  }
  if(layerId==='drone'){
    return {tag:'Drone',name:'Model M Quad',meta:'Heading '+feat.heading+' deg',
      kpis:scoreKpi.concat(LAYER_INSIGHTS.drone.kpis),recs:[],anom:[],alerts:LAYER_INSIGHTS.drone.alerts};
  }
  if(layerId==='stockpiles'){
    return {tag:'Stockpile',name:feat.name,meta:'Grade '+feat.grade+' material',
      kpis:scoreKpi.concat([['Volume',feat.vol+' m^3'],['Grade',feat.grade],['Method','TIN']]),
      recs:anomArr.length?['Investigate flagged anomaly before reconciliation.']:['Within +/-3% accuracy band. Ready for reconciliation.'],
      anom:anomArr,alerts:[]};
  }
  if(layerId==='pits'){
    return {tag:'Pit',name:feat.name,meta:'Depth '+feat.depth+' m',
      kpis:scoreKpi.concat([['Depth',feat.depth+' m'],['Plan Dev.','+1.8%'],['Slope Angle',feat.anomaly?'47 deg':'42 deg']]),
      recs:anomArr.length?['Review slope stability and resurvey.']:['Compliance to plan within tolerance.'],
      anom:anomArr,alerts:[]};
  }
  if(layerId==='dumps'){
    return {tag:'Waste Dump',name:feat.name,meta:'Volume '+feat.vol+' m^3'+(feat.flag?' (provisional)':''),
      kpis:scoreKpi.concat([['Volume',feat.vol+' m^3'],['Status',feat.flag||'Confirmed'],['Method','TIN']]),
      recs:anomArr.length?['Validate boundary before submission.']:['Ready for monthly report.'],
      anom:anomArr,alerts:[]};
  }
  if(layerId==='cutfill'){
    return {tag:'Cut-Fill',name:feat.name,meta:feat.delta+' sigma deviation',
      kpis:scoreKpi.concat([['Expected','312 m^3'],['Observed','97 m^3'],['Delta','-215 m^3'],['Sigma',feat.delta+'']]),
      recs:['Re-fly before certifying cut-fill.'],anom:anomArr,alerts:['Investigate before delivery.']};
  }
  // Heavy layers and non-feature layers
  return srLayerLevelDetail(layerId);
}

// Build a layer-level det from LAYER_INSIGHTS — used for single-active-layer focus and
// for heavy/flat layers (Ortho/DSM/DTM/Mesh/PCD/Images/Flight) that have no per-feature data.
// Works for ALL 14 layer IDs.
function srLayerLevelDetail(layerId){
  var ins = LAYER_INSIGHTS[layerId];
  if(!ins) return null;
  var dispName = layerId;
  for(var g=0; g<LAYERS.length; g++){
    for(var i=0; i<LAYERS[g].items.length; i++){
      if(LAYERS[g].items[i].id===layerId){ dispName = LAYERS[g].items[i].name; break; }
    }
  }
  var displayScoreKpi = (typeof ins.score === 'number') ? [['Score', ins.score + ' / 100']] : [];
  return {
    tag: dispName,
    name: dispName,
    meta: ins.meta || '',
    kpis: displayScoreKpi.concat(ins.kpis || []),
    recs: ins.recs || [],
    anom: ins.anom || [],
    alerts: ins.alerts || []
  };
}

var SR={
  canvas:null, ctx:null, ovl:null,
  w:0, h:0, dpr:1,
  pan:{x:0,y:0}, zoom:1,
  drag:null,
  drone:{x:.55,y:.18,t:0},
  selected:null, // {layerId, feat}
  mode:'default'  // 'default' | 'score' | 'anomalies'
};

function setSRMode(m){
  if(m!=='default' && m!=='score' && m!=='anomalies') return;
  if(SR.mode===m) return;
  SR.mode = m;
  document.getElementById('sr-mbtn-default').classList.toggle('active', m==='default');
  document.getElementById('sr-mbtn-score').classList.toggle('active', m==='score');
  document.getElementById('sr-mbtn-anom').classList.toggle('active', m==='anomalies');
  // Update scene rendering for the new mode
  if(typeof srSceneRender==='function') srSceneRender();
  if(typeof drawOverlay==='function') drawOverlay();
  buildInsights();
}

// Backward-compat shims for any old code paths that still read showScores/showAnomalies.
// Anomalies mode shows anomalies; Score and Default modes show scores in the existing
// buildInsights/drawOverlay code. Default and Score differ in scene treatment only.
Object.defineProperty(SR, 'showScores', {
  get: function(){ return SR.mode === 'score' || SR.mode === 'default'; },
  configurable: true
});
Object.defineProperty(SR, 'showAnomalies', {
  get: function(){ return SR.mode === 'anomalies'; },
  configurable: true
});

// helpers for anomaly mode
function featureHasAnomaly(feat){ return !!(feat && feat.anomaly); }
function featureScore(feat){ return (feat && typeof feat.score==='number')?feat.score:null; }
function scoreColor(s){
  // monochrome ramp -- brighter = better. Cyan accent only for top tier.
  if(s>=90) return 'rgba(0,180,216,.85)';
  if(s>=80) return 'rgba(255,255,255,.78)';
  if(s>=70) return 'rgba(255,255,255,.55)';
  return 'rgba(255,255,255,.38)';
}

function srResize(){
  var c=SR.canvas; if(!c) return;
  var rect=c.getBoundingClientRect();
  SR.dpr=Math.min(2,window.devicePixelRatio||1);
  SR.w=rect.width; SR.h=rect.height;
  // canvas is hidden (display:none) and the SVG uses a fixed 1000x700 viewBox
  // that scales automatically via preserveAspectRatio="xMidYMid meet".
  // We only update SR.w/SR.h so any legacy code that reads them still gets values.
  if(typeof srSceneRender === 'function') srSceneRender();
}

// Procedural terrain: smooth pseudo-noise hillshading, earth tones
function drawTerrain(){
  var ctx=SR.ctx, W=SR.w, H=SR.h;
  // sky-to-ground gradient base
  var g=ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'#0c1119'); g.addColorStop(1,'#161c24');
  ctx.fillStyle=g; ctx.fillRect(0,0,W,H);

  // hillshade via pseudo-noise: layered sine fields
  var img=ctx.getImageData(0,0,W,H);
  var d=img.data;
  for(var y=0;y<H;y+=2){
    for(var x=0;x<W;x+=2){
      var nx=x/W, ny=y/H;
      // multi-octave pseudo-noise
      var h=
        Math.sin(nx*6.2+ny*4.3)*0.5+
        Math.sin(nx*13.1-ny*9.7+1.2)*0.28+
        Math.sin(nx*27.4+ny*23.1+2.4)*0.14+
        Math.sin(nx*54+ny*48+3.7)*0.07;
      // simulate slope (gradient) for shading
      var sx=Math.cos(nx*6.2+ny*4.3)*6.2*.5 + Math.cos(nx*13.1-ny*9.7+1.2)*13.1*.28;
      var sy=Math.sin(nx*6.2+ny*4.3)*4.3*.5 + Math.sin(nx*13.1-ny*9.7+1.2)*-9.7*.28;
      // light from NW
      var lit=(sx*0.7-sy*0.7+8)/16;
      lit=Math.max(0,Math.min(1,lit));
      // colour ramp: low=greyish, mid=warm tan, high=lighter
      var elev=(h+1)*.5;
      var r=Math.floor(28 + elev*42 + lit*38);
      var gg=Math.floor(32 + elev*36 + lit*32);
      var b=Math.floor(38 + elev*22 + lit*22);
      // fill 2x2 block
      for(var dy=0;dy<2;dy++)for(var dx=0;dx<2;dx++){
        var idx=((y+dy)*W+(x+dx))*4;
        if(idx<d.length){d[idx]=r;d[idx+1]=gg;d[idx+2]=b;d[idx+3]=255;}
      }
    }
  }
  ctx.putImageData(img,0,0);

  // subtle vignette
  var vg=ctx.createRadialGradient(W/2,H/2,W*.3,W/2,H/2,W*.75);
  vg.addColorStop(0,'rgba(0,0,0,0)'); vg.addColorStop(1,'rgba(0,0,0,.5)');
  ctx.fillStyle=vg; ctx.fillRect(0,0,W,H);

  // grid lines (graticule) -- very faint
  ctx.strokeStyle='rgba(255,255,255,.04)'; ctx.lineWidth=.5;
  for(var i=1;i<8;i++){
    ctx.beginPath(); ctx.moveTo(W*i/8,0); ctx.lineTo(W*i/8,H); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0,H*i/8); ctx.lineTo(W,H*i/8); ctx.stroke();
  }
}

// Helper: map normalised (0..1) coord to canvas px
function srPt(p){return [p[0]*SR.w, p[1]*SR.h];}
function srXY(x,y){return [x*SR.w, y*SR.h];}

// Site Reality scene rendering — replaces the old canvas + SVG-overlay system.
// The SVG #sr-overlay has fixed viewBox 0 0 1000 700; it acts as the full scene.
// srSceneRender() is called whenever visibility, focus, or mode changes.
function drawOverlay(){
  // Legacy entry point. Routed to the new render path.
  if(typeof srSceneRender==='function') srSceneRender();
}

// Coordinate transformation: FEATURES are stored in normalised 0..1 space.
// The new scene uses a fixed 1000×700 viewBox with terrain at 120,80→880,580.
// So x=120 + nx*760, y=80 + ny*500
function srNX(nx){ return 120 + nx*760; }
function srNY(ny){ return 80 + ny*500; }
function srNP(p){ return [srNX(p[0]), srNY(p[1])]; }

// Helper: namespaced SVG element factory
function srEl(tag, attrs){
  var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  if(attrs){ for(var k in attrs) el.setAttribute(k, attrs[k]); }
  return el;
}

// Helper: clear a group's children
function srClearGroup(g){
  if(!g) return;
  while(g.firstChild) g.removeChild(g.firstChild);
}

// PHASE 1 MAIN RENDER ENTRY POINT
// Subsequent phases extend this to call per-stage renderers.
function srSceneRender(){
  var svg = document.getElementById('sr-overlay');
  if(!svg) return;
  var stageGroup = document.getElementById('sr-scene-stage');
  var emptyGroup = document.getElementById('sr-scene-empty');
  if(!stageGroup) return;

  // Set the scene's data-mode for CSS selectors
  svg.setAttribute('data-mode', SR.mode || 'default');

  // Clear stage content
  srClearGroup(stageGroup);

  // Determine which objects are visible
  var anyVisible = false;
  LAYERS.forEach(function(grp){
    grp.items.forEach(function(L){
      if(L.on){
        // For multi-layers, also check if at least one child is visible
        if(L.multi){
          var map = SR_LAYER_UI.childVisible[L.id] || {};
          var feats = FEATURES[L.id] || [];
          if(feats.some(function(f){ return map[f.id]; })) anyVisible = true;
        } else {
          anyVisible = true;
        }
      }
    });
  });

  if(emptyGroup) emptyGroup.style.display = anyVisible ? 'none' : '';

  // Render each stage
  srRenderCaptureStage(stageGroup);
  srRenderProcessingStage(stageGroup);
  srRenderAnalyticsStage(stageGroup);

  // Update scrubber visibility / state for Capture replay
  srEnsureScrubber();
  srUpdateScrubberState();
}

function srRenderPhase1Chip(parent, label, x, y, stageLabel){
  var stageColor = stageLabel === 'Capture' ? '#94D4E8' : stageLabel === 'Processing' ? '#7CB89A' : '#D2AA4E';
  var width = 8 + label.length * 7.5;
  var g = srEl('g', {transform: 'translate('+x+','+y+')'});
  var rect = srEl('rect', {
    x: 0, y: 0, width: width, height: 20, rx: 3,
    fill: 'rgba(2, 3, 8, 0.78)',
    stroke: stageColor, 'stroke-opacity': 0.6, 'stroke-width': 0.6
  });
  g.appendChild(rect);
  var dot = srEl('circle', {cx: 8, cy: 10, r: 2.5, fill: stageColor, opacity: 0.85});
  g.appendChild(dot);
  var text = srEl('text', {
    x: 16, y: 13, fill: 'rgba(235, 242, 248, 0.85)',
    'font-family': 'Inter', 'font-size': 10
  });
  text.textContent = label;
  g.appendChild(text);
  parent.appendChild(g);
}
// ============================================================
// PHASE 2 — CAPTURE STAGE RENDERING
// Renders Drone, Flight Path, Base Station, Control Points as inline SVG
// within the fixed-viewBox scene. Uses FEATURES data (normalised 0..1).
// Coordinate transform via srNX/srNY (Phase 1 helpers).
// ============================================================

// Replay state for Capture (survey clock)
var SR_CAPTURE = {
  t: 0,            // 0..1 along the flight path
  playing: false,
  speed: 1,        // 1x, 2x, 4x, 8x
  duration: 1440,  // 24 minutes in seconds (display only)
  lastFrameTime: null,
  animFrame: null
};

// Synthesised drone anomalies along the flight (timing only — the existing
// FEATURES doesn't carry per-timestamp anomalies, so we generate reasonable ones).
var SR_DRONE_ANOMALIES = [
  { id:'DA1', t:0.13, severity:'warn', type:'Front overlap below target', detail:'Front overlap 68% on line 2 (target 75%)', timestamp:'03:07' },
  { id:'DA2', t:0.27, severity:'warn', type:'Altitude AGL deviation',     detail:'Altitude 12% above planned for 4 samples',  timestamp:'06:29' },
  { id:'DA3', t:0.42, severity:'crit', type:'GNSS fix lost (drone)',      detail:'Lost-fix duration 2.4 seconds during line 5 turnaround', timestamp:'10:04' },
  { id:'DA4', t:0.56, severity:'warn', type:'Side overlap below target',  detail:'Side overlap 64% between lines 5 and 6 (target 70%)', timestamp:'13:25' },
  { id:'DA5', t:0.68, severity:'warn', type:'Gimbal angle deviation',     detail:'Gimbal pitch 7.2 degrees from nadir on 3 captures', timestamp:'16:18' },
  { id:'DA6', t:0.82, severity:'warn', type:'Yaw rate excessive',         detail:'Yaw rate 34 deg/s on line 9 turn', timestamp:'19:41' }
];

// Synthesised base station anomalies
var SR_BASE_ANOMALIES = [
  { id:'BA1', tStart:0.41, tEnd:0.44, t:0.41, severity:'warn', type:'RTK fix lost',          detail:'RTK-float for 2.6 seconds; recovered to fixed', timestamp:'09:50 - 10:23' },
  { id:'BA2', tStart:0.55, tEnd:0.58, t:0.55, severity:'warn', type:'Insufficient satellites', detail:'7 satellites tracked (target 8+); brief tree shadow', timestamp:'13:12 - 13:55' }
];

// Compute flight path segments for arc-length parameterisation
var SR_PATH_SEGMENTS = null;
var SR_PATH_TOTAL = 0;

function srBuildPathSegments(){
  if(SR_PATH_SEGMENTS) return;
  var pts = FEATURES.flight || [];
  if(pts.length < 2) return;
  SR_PATH_SEGMENTS = [];
  SR_PATH_TOTAL = 0;
  for(var i=0; i<pts.length-1; i++){
    var p1 = pts[i], p2 = pts[i+1];
    var x1 = srNX(p1[0]), y1 = srNY(p1[1]);
    var x2 = srNX(p2[0]), y2 = srNY(p2[1]);
    var len = Math.hypot(x2-x1, y2-y1);
    SR_PATH_SEGMENTS.push({ x1:x1, y1:y1, x2:x2, y2:y2, len:len, cumStart:SR_PATH_TOTAL });
    SR_PATH_TOTAL += len;
  }
}

function srPositionAt(t){
  srBuildPathSegments();
  if(!SR_PATH_SEGMENTS || !SR_PATH_SEGMENTS.length) return {x:500, y:340, heading:0};
  if(t <= 0){
    var seg = SR_PATH_SEGMENTS[0];
    return {x:seg.x1, y:seg.y1, heading:Math.atan2(seg.y2-seg.y1, seg.x2-seg.x1)};
  }
  if(t >= 1){
    var last = SR_PATH_SEGMENTS[SR_PATH_SEGMENTS.length-1];
    return {x:last.x2, y:last.y2, heading:Math.atan2(last.y2-last.y1, last.x2-last.x1)};
  }
  var target = t * SR_PATH_TOTAL;
  for(var i=0; i<SR_PATH_SEGMENTS.length; i++){
    var seg = SR_PATH_SEGMENTS[i];
    if(target >= seg.cumStart && target <= seg.cumStart + seg.len){
      var localT = (target - seg.cumStart) / seg.len;
      return {
        x: seg.x1 + (seg.x2-seg.x1)*localT,
        y: seg.y1 + (seg.y2-seg.y1)*localT,
        heading: Math.atan2(seg.y2-seg.y1, seg.x2-seg.x1)
      };
    }
  }
  return {x:500, y:340, heading:0};
}

function srFanQualityAt(t){
  for(var i=0; i<SR_DRONE_ANOMALIES.length; i++){
    var a = SR_DRONE_ANOMALIES[i];
    if(Math.abs(a.t - t) < 0.025) return a.severity;
  }
  return 'good';
}

function srFormatTime(sec){
  var m = Math.floor(sec/60), s = Math.floor(sec%60);
  return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
}

// ============================================================
// DEFS — add Capture-specific gradients & filters once
// ============================================================
function srEnsureCaptureDefs(){
  var defs = document.querySelector('#sr-overlay defs');
  if(!defs || document.getElementById('sr-cap-fanGrad')) return;

  var defsHtml =
    '<linearGradient id="sr-cap-fanGrad" x1="0%" y1="0%" x2="0%" y2="100%">' +
      '<stop offset="0%" stop-color="#94D4E8" stop-opacity="0.55"/>' +
      '<stop offset="100%" stop-color="#94D4E8" stop-opacity="0.15"/>' +
    '</linearGradient>' +
    '<linearGradient id="sr-cap-fanGrad-good" x1="0%" y1="0%" x2="0%" y2="100%">' +
      '<stop offset="0%" stop-color="#7CB89A" stop-opacity="0.7"/>' +
      '<stop offset="100%" stop-color="#7CB89A" stop-opacity="0.25"/>' +
    '</linearGradient>' +
    '<linearGradient id="sr-cap-fanGrad-warn" x1="0%" y1="0%" x2="0%" y2="100%">' +
      '<stop offset="0%" stop-color="#D2AA4E" stop-opacity="0.7"/>' +
      '<stop offset="100%" stop-color="#D2AA4E" stop-opacity="0.25"/>' +
    '</linearGradient>' +
    '<linearGradient id="sr-cap-fanGrad-crit" x1="0%" y1="0%" x2="0%" y2="100%">' +
      '<stop offset="0%" stop-color="#C86262" stop-opacity="0.7"/>' +
      '<stop offset="100%" stop-color="#C86262" stop-opacity="0.25"/>' +
    '</linearGradient>' +
    '<radialGradient id="sr-cap-coneGrad" cx="50%" cy="100%" r="80%">' +
      '<stop offset="0%" stop-color="#94D4E8" stop-opacity="0.45"/>' +
      '<stop offset="100%" stop-color="#94D4E8" stop-opacity="0.05"/>' +
    '</radialGradient>' +
    '<radialGradient id="sr-cap-coneGrad-warn" cx="50%" cy="100%" r="80%">' +
      '<stop offset="0%" stop-color="#D2AA4E" stop-opacity="0.5"/>' +
      '<stop offset="100%" stop-color="#D2AA4E" stop-opacity="0.05"/>' +
    '</radialGradient>';

  // Append by parsing
  var temp = document.createElementNS('http://www.w3.org/2000/svg','svg');
  temp.innerHTML = defsHtml;
  while(temp.firstChild) defs.appendChild(temp.firstChild);
}

// ============================================================
// PER-OBJECT RENDERERS
// ============================================================

function srRenderFlightPath(parent){
  var pts = FEATURES.flight || [];
  if(pts.length < 2) return;
  var d = '';
  for(var i=0; i<pts.length; i++){
    d += (i===0?'M ':' L ') + srNX(pts[i][0]) + ' ' + srNY(pts[i][1]);
  }
  // Planned route — dashed
  var path = srEl('path', {
    d: d,
    fill: 'none',
    stroke: 'rgba(148, 212, 232, 0.35)',
    'stroke-width': '1',
    'stroke-dasharray': '6 4'
  });
  parent.appendChild(path);

  // Waypoints
  for(var j=0; j<pts.length; j++){
    var isEndpoint = (j===0 || j===pts.length-1);
    var radius = isEndpoint ? 4 : 2.5;
    var cx = srNX(pts[j][0]), cy = srNY(pts[j][1]);
    var wp = srEl('circle', {
      cx: cx, cy: cy, r: radius,
      fill: 'rgba(148, 212, 232, 0.65)',
      stroke: 'rgba(235, 242, 248, 0.4)',
      'stroke-width': '0.5',
      'data-layer': 'flight',
      'data-feat': 'WP-' + (j+1),
      style: 'cursor: pointer;'
    });
    parent.appendChild(wp);

    if(isEndpoint){
      var label = srEl('text', {
        x: cx, y: cy - 8,
        'text-anchor': 'middle',
        fill: 'rgba(148, 212, 232, 0.65)',
        'font-family': 'IBM Plex Mono', 'font-size': '8'
      });
      label.textContent = (j===0 ? 'TAKEOFF' : 'LANDING');
      parent.appendChild(label);
    }
  }
}

function srRenderDrone(parent){
  // Actual path (solid) under the drone — used only when drone visible
  var pts = FEATURES.flight || [];
  if(pts.length >= 2){
    var pathD = '';
    for(var i=0; i<pts.length; i++){
      pathD += (i===0?'M ':' L ') + srNX(pts[i][0]) + ' ' + srNY(pts[i][1]);
    }
    var actualPath = srEl('path', {
      d: pathD,
      fill: 'none',
      stroke: '#94D4E8',
      'stroke-width': '1.5',
      'stroke-linecap': 'round',
      opacity: SR.mode === 'anomalies' ? '0.25' : '1'
    });
    // arc-length-based dash for "completed up to t"
    srBuildPathSegments();
    actualPath.style.strokeDasharray = SR_PATH_TOTAL;
    actualPath.style.strokeDashoffset = SR_PATH_TOTAL * (1 - SR_CAPTURE.t);
    parent.appendChild(actualPath);
  }

  // Coverage fan at current position
  var pos = srPositionAt(SR_CAPTURE.t);
  srRenderCoverageFan(parent, pos);

  // Drone body
  var drone = srEl('g', {
    transform: 'translate(' + pos.x + ',' + pos.y + ') rotate(' + (pos.heading*180/Math.PI + 90) + ')',
    'data-layer': 'drone',
    'data-feat': 'drone',
    style: 'cursor: pointer;'
  });

  var nearAnomaly = SR_DRONE_ANOMALIES.some(function(a){ return Math.abs(a.t - SR_CAPTURE.t) < 0.02; });
  var droneOpacity = (SR.mode === 'anomalies' && !nearAnomaly) ? 0.35 : 1;
  drone.setAttribute('opacity', droneOpacity);

  drone.appendChild(srEl('circle', {cx:0, cy:0, r:14, fill:'rgba(148, 212, 232, 0.12)'}));
  var body = srEl('g', {filter:'url(#sr-drop-shadow)'});
  body.appendChild(srEl('line', {x1:-9, y1:-9, x2:9, y2:9, stroke:'rgba(235,242,248,0.7)', 'stroke-width':'1.2'}));
  body.appendChild(srEl('line', {x1:9, y1:-9, x2:-9, y2:9, stroke:'rgba(235,242,248,0.7)', 'stroke-width':'1.2'}));
  ['-9,-9','9,-9','-9,9','9,9'].forEach(function(coords){
    var c = coords.split(',');
    body.appendChild(srEl('circle', {cx:c[0], cy:c[1], r:3.5, fill:'rgba(235,242,248,0.85)', stroke:'rgba(148,212,232,0.5)', 'stroke-width':'0.5'}));
  });
  body.appendChild(srEl('circle', {cx:0, cy:0, r:4, fill:'#94D4E8', stroke:'white', 'stroke-width':'0.5'}));
  body.appendChild(srEl('circle', {cx:0, cy:-2, r:1, fill:'white'}));
  drone.appendChild(body);
  parent.appendChild(drone);

  // Anomaly markers along the path (show as small markers; full at .5 in default mode, full at 1.0 in anomalies mode)
  SR_DRONE_ANOMALIES.forEach(function(a){
    if(SR.mode === 'score') return; // score mode hides anomaly markers
    var ap = srPositionAt(a.t);
    var g = srEl('g', {transform: 'translate('+ap.x+','+ap.y+')', style:'cursor: pointer;'});
    g.setAttribute('opacity', SR.mode === 'anomalies' ? '1' : '0.5');
    g.appendChild(srEl('circle', {r:5, fill:'rgba(2, 3, 8, 0.6)', stroke:'white', 'stroke-width':'0.8'}));
    g.appendChild(srEl('circle', {r:3.5, fill: a.severity === 'crit' ? '#C86262' : '#D2AA4E'}));
    g.setAttribute('data-anomaly-t', a.t);
    g.setAttribute('data-anomaly-type', a.type);
    g.setAttribute('data-anomaly-detail', a.detail);
    g.setAttribute('data-anomaly-severity', a.severity);
    parent.appendChild(g);
  });
}

function srRenderCoverageFan(parent, pos){
  var fanLength = 36, fanHalfWidth = 18;
  var quality = srFanQualityAt(SR_CAPTURE.t);
  var fillUrl = 'url(#sr-cap-fanGrad)';
  if(SR.mode === 'score'){
    if(quality==='crit') fillUrl = 'url(#sr-cap-fanGrad-crit)';
    else if(quality==='warn') fillUrl = 'url(#sr-cap-fanGrad-warn)';
    else fillUrl = 'url(#sr-cap-fanGrad-good)';
  }
  if(SR.mode === 'anomalies' && quality === 'good') return;

  var points = [[-fanHalfWidth*0.3, 4], [fanHalfWidth*0.3, 4], [fanHalfWidth, fanLength], [-fanHalfWidth, fanLength]];
  var cosA = Math.cos(pos.heading), sinA = Math.sin(pos.heading);
  var rotated = points.map(function(p){
    return [pos.x + (p[0]*cosA - p[1]*sinA), pos.y + (p[0]*sinA + p[1]*cosA)];
  });
  var fan = srEl('polygon', {
    points: rotated.map(function(p){return p.join(',');}).join(' '),
    fill: fillUrl,
    stroke: quality==='warn'?'rgba(210,170,78,0.7)':quality==='crit'?'rgba(200,98,98,0.7)':'rgba(148,212,232,0.55)',
    'stroke-width':'0.8'
  });
  parent.appendChild(fan);
}

function srRenderBase(parent){
  var b = FEATURES.base;
  if(!b) return;
  var bx = srNX(b.x), by = srNY(b.y);

  // Signal cone — quality depends on current time vs base anomalies
  var t = SR_CAPTURE.t, baseQuality = 'good';
  for(var i=0; i<SR_BASE_ANOMALIES.length; i++){
    var a = SR_BASE_ANOMALIES[i];
    if(t >= a.tStart && t <= a.tEnd){ baseQuality = a.severity; break; }
  }
  var coneHeight = 80;
  var coneHalfWidth = baseQuality === 'warn' ? 22 : 32;
  var apexX = bx, apexY = by - 15;

  // In anomalies mode, hide cone unless degraded
  var showCone = !(SR.mode === 'anomalies' && baseQuality === 'good');
  if(showCone){
    var coneFill = baseQuality === 'warn' ? 'url(#sr-cap-coneGrad-warn)' : 'url(#sr-cap-coneGrad)';
    var coneStroke = baseQuality === 'warn' ? 'rgba(210,170,78,0.6)' : 'rgba(148,212,232,0.5)';
    var cone = srEl('polygon', {
      points: apexX+','+apexY+' '+(apexX-coneHalfWidth)+','+(apexY-coneHeight)+' '+(apexX+coneHalfWidth)+','+(apexY-coneHeight),
      fill: coneFill, stroke: coneStroke, 'stroke-width':'0.8'
    });
    parent.appendChild(cone);
  }

  // Antenna
  var antenna = srEl('g', {
    transform: 'translate('+bx+','+by+')',
    'data-layer':'base', 'data-feat':'base',
    style: 'cursor: pointer;'
  });
  var antOpacity = (SR.mode === 'anomalies' && baseQuality === 'good') ? 0.5 : 1;
  antenna.setAttribute('opacity', antOpacity);
  antenna.appendChild(srEl('line', {x1:0, y1:-2, x2:-8, y2:14, stroke:'rgba(235,242,248,0.7)', 'stroke-width':'1'}));
  antenna.appendChild(srEl('line', {x1:0, y1:-2, x2:8, y2:14, stroke:'rgba(235,242,248,0.7)', 'stroke-width':'1'}));
  antenna.appendChild(srEl('line', {x1:0, y1:-2, x2:0, y2:14, stroke:'rgba(235,242,248,0.7)', 'stroke-width':'1'}));
  antenna.appendChild(srEl('line', {x1:0, y1:-2, x2:0, y2:-14, stroke:'rgba(235,242,248,0.85)', 'stroke-width':'1.5'}));
  antenna.appendChild(srEl('ellipse', {cx:0, cy:-15, rx:6, ry:2, fill:'rgba(148,212,232,0.7)', stroke:'white', 'stroke-width':'0.5'}));
  antenna.appendChild(srEl('ellipse', {cx:0, cy:-16, rx:4, ry:1.2, fill:'rgba(235,242,248,0.85)'}));
  var label = srEl('text', {x:0, y:22, 'text-anchor':'middle', fill:'rgba(235,242,248,0.55)', 'font-family':'IBM Plex Mono', 'font-size':'7', 'letter-spacing':'0.05em'});
  label.textContent = 'BASE';
  antenna.appendChild(label);
  parent.appendChild(antenna);
}

function srRenderGcps(parent){
  var gcps = FEATURES.gcps || [];
  // Mark the two worst-residual Control Points as check (the prototype distinguishes control vs check)
  var sorted = gcps.slice().sort(function(a,b){ return b.res - a.res; });
  var checkSet = {};
  if(sorted[0]) checkSet[sorted[0].id] = true;
  if(sorted[1]) checkSet[sorted[1].id] = true;

  gcps.forEach(function(g){
    var gx = srNX(g.x), gy = srNY(g.y);
    var isCheck = !!checkSet[g.id];
    var state = (g.score < 70 || (g.res||0) >= 14) ? 'crit' : (g.anomaly || g.score < 85) ? 'warn' : 'good';
    var stateColor = state === 'good' ? '#94D4E8' : state === 'warn' ? '#D2AA4E' : '#C86262';

    // In anomalies mode, dim passing Control Points to 0.25
    var gcpOpacity = (SR.mode === 'anomalies' && state === 'good') ? 0.25 : 1;

    var gcpG = srEl('g', {
      transform: 'translate('+gx+','+gy+')',
      opacity: gcpOpacity,
      'data-layer':'gcps', 'data-feat':g.id,
      style: 'cursor: pointer;'
    });

    // XY halo (radius proportional to XY residual)
    var haloRadius = Math.min((g.res||5) * 1.2, 18);
    gcpG.appendChild(srEl('circle', {
      r: haloRadius,
      fill: 'none',
      stroke: stateColor,
      'stroke-width':'0.8',
      opacity:'0.5'
    }));

    // Z needle (rough proxy: half of XY residual)
    var needleHeight = Math.min((g.res||5) * 1.5, 22);
    gcpG.appendChild(srEl('line', {
      x1:0, y1:0, x2:0, y2:-needleHeight,
      stroke: stateColor, 'stroke-width':'1.5'
    }));

    // Disc
    var discFill = SR.mode === 'score'
      ? (state === 'good' ? '#7CB89A' : state === 'warn' ? '#D2AA4E' : '#C86262')
      : (isCheck ? 'rgba(2, 3, 8, 0.6)' : 'rgba(148, 212, 232, 0.5)');

    var disc = srEl('circle', {
      r:6, fill: discFill, stroke:'white', 'stroke-width':'0.5'
    });
    if(isCheck) disc.setAttribute('stroke-dasharray', '2 1');
    gcpG.appendChild(disc);
    gcpG.appendChild(srEl('circle', {r:2, fill:'white'}));

    // Label
    var labelY = gy < 200 ? -14 : 24;
    var lbl = srEl('text', {
      y: labelY, 'text-anchor':'middle',
      fill:'rgba(235, 242, 248, 0.55)',
      'font-family':'IBM Plex Mono', 'font-size':'7', 'letter-spacing':'0.05em'
    });
    lbl.textContent = g.id;
    gcpG.appendChild(lbl);

    // Anomaly callout in Anomalies mode
    if(SR.mode === 'anomalies' && state !== 'good'){
      var calloutY = gy < 200 ? -28 : 38;
      var callout = srEl('text', {
        y: calloutY, 'text-anchor':'middle',
        fill: stateColor,
        'font-family':'IBM Plex Mono', 'font-size':'8', 'font-weight':'600'
      });
      callout.textContent = (g.res||0) + 'mm';
      gcpG.appendChild(callout);
    }

    parent.appendChild(gcpG);
  });

  // In Anomalies mode, draw connecting lines from failing check points to nearest controls
  if(SR.mode === 'anomalies'){
    var failingChecks = gcps.filter(function(g){ return checkSet[g.id] && (g.score < 85 || g.anomaly); });
    var controls = gcps.filter(function(g){ return !checkSet[g.id]; });
    failingChecks.forEach(function(cp){
      var cpX = srNX(cp.x), cpY = srNY(cp.y);
      var withDist = controls.map(function(c){
        return { c:c, d: Math.hypot(srNX(c.x) - cpX, srNY(c.y) - cpY) };
      }).sort(function(a,b){return a.d - b.d;}).slice(0, 3);
      withDist.forEach(function(item){
        parent.appendChild(srEl('line', {
          x1: cpX, y1: cpY,
          x2: srNX(item.c.x), y2: srNY(item.c.y),
          stroke: '#C86262', 'stroke-width':'0.5',
          'stroke-dasharray':'3 2', opacity:'0.6'
        }));
      });
    });
  }
}

// ============================================================
// CAPTURE STAGE RENDER ENTRY
// ============================================================
function srRenderCaptureStage(stageGroup){
  srEnsureCaptureDefs();
  var drone = srFindLayer('drone');
  var flight = srFindLayer('flight');
  var base = srFindLayer('base');
  var gcps = srFindLayer('gcps');

  if(flight && flight.on){
    var fpG = srEl('g', {});
    srRenderFlightPath(fpG);
    stageGroup.appendChild(fpG);
  }

  if(drone && drone.on){
    var drG = srEl('g', {});
    srRenderDrone(drG);
    stageGroup.appendChild(drG);
  }

  if(base && base.on){
    var bsG = srEl('g', {});
    srRenderBase(bsG);
    stageGroup.appendChild(bsG);
  }

  if(gcps && gcps.on){
    var gcG = srEl('g', {});
    srRenderGcps(gcG);
    stageGroup.appendChild(gcG);
  }
}

// ============================================================
// SCRUBBER — built lazily, shown/hidden based on Capture replay-driven object visibility
// ============================================================
function srEnsureScrubber(){
  var existing = document.getElementById('sr-scrubber');
  if(existing) return existing;

  var view = document.getElementById('view-sr');
  if(!view) return null;

  var s = document.createElement('div');
  s.id = 'sr-scrubber';
  s.className = 'sr-scrubber';
  s.innerHTML =
    '<button class="sr-scb-play" id="sr-scb-play" title="Play / Pause">' +
      '<svg id="sr-scb-icon" width="14" height="14" viewBox="0 0 14 14" fill="currentColor"><path d="M3 2L11 7L3 12V2Z"/></svg>' +
    '</button>' +
    '<div class="sr-scb-time" id="sr-scb-time">00:00</div>' +
    '<div class="sr-scb-track-wrap"><div class="sr-scb-track" id="sr-scb-track">' +
      '<div class="sr-scb-progress" id="sr-scb-progress"></div>' +
      '<div id="sr-scb-ticks"></div>' +
      '<div class="sr-scb-playhead" id="sr-scb-playhead"></div>' +
    '</div></div>' +
    '<div class="sr-scb-time">24:00</div>' +
    '<button class="sr-scb-speed" id="sr-scb-speed">1x</button>';
  view.appendChild(s);

  // Wire interactions
  document.getElementById('sr-scb-play').addEventListener('click', function(){
    if(SR_CAPTURE.t >= 1) SR_CAPTURE.t = 0;
    SR_CAPTURE.playing = !SR_CAPTURE.playing;
    SR_CAPTURE.lastFrameTime = null;
    srUpdatePlayButton();
    if(SR_CAPTURE.playing) srTickReplay();
  });
  document.getElementById('sr-scb-speed').addEventListener('click', function(){
    var speeds = [1, 2, 4, 8];
    var idx = speeds.indexOf(SR_CAPTURE.speed);
    SR_CAPTURE.speed = speeds[(idx + 1) % speeds.length];
    this.textContent = SR_CAPTURE.speed + 'x';
  });
  var track = document.getElementById('sr-scb-track');
  var dragging = false;
  function setTFromEvent(e){
    var rect = track.getBoundingClientRect();
    var x = (e.clientX || (e.touches && e.touches[0].clientX)) - rect.left;
    SR_CAPTURE.t = Math.max(0, Math.min(1, x / rect.width));
    srSceneRender();
  }
  track.addEventListener('mousedown', function(e){
    dragging = true;
    SR_CAPTURE.playing = false;
    srUpdatePlayButton();
    setTFromEvent(e);
  });
  document.addEventListener('mousemove', function(e){ if(dragging) setTFromEvent(e); });
  document.addEventListener('mouseup', function(){ dragging = false; });

  return s;
}

function srUpdatePlayButton(){
  var icon = document.getElementById('sr-scb-icon');
  if(!icon) return;
  if(SR_CAPTURE.playing){
    icon.innerHTML = '<path d="M3 2H5V12H3V2ZM9 2H11V12H9V2Z"/>';
  } else {
    icon.innerHTML = '<path d="M3 2L11 7L3 12V2Z"/>';
  }
}

function srTickReplay(){
  if(!SR_CAPTURE.playing) return;
  var now = performance.now();
  if(SR_CAPTURE.lastFrameTime === null) SR_CAPTURE.lastFrameTime = now;
  var dt = (now - SR_CAPTURE.lastFrameTime) / 1000;
  SR_CAPTURE.lastFrameTime = now;
  SR_CAPTURE.t += (dt / SR_CAPTURE.duration) * SR_CAPTURE.speed;
  if(SR_CAPTURE.t >= 1){
    SR_CAPTURE.t = 1;
    SR_CAPTURE.playing = false;
    srUpdatePlayButton();
  }
  srSceneRender();
  if(SR_CAPTURE.playing) SR_CAPTURE.animFrame = requestAnimationFrame(srTickReplay);
}

function srUpdateScrubberState(){
  var s = document.getElementById('sr-scrubber');
  if(!s) return;
  // Show scrubber if any replay-driven object visible (drone or base for now)
  var drone = srFindLayer('drone'), base = srFindLayer('base'), gcps = srFindLayer('gcps');
  var show = (drone && drone.on) || (base && base.on) || (gcps && gcps.on);
  s.classList.toggle('show', show);
  if(!show && SR_CAPTURE.playing){
    SR_CAPTURE.playing = false;
    srUpdatePlayButton();
  }
  // Update progress + playhead
  var pct = SR_CAPTURE.t * 100;
  var prog = document.getElementById('sr-scb-progress');
  var head = document.getElementById('sr-scb-playhead');
  if(prog) prog.style.width = pct + '%';
  if(head) head.style.left = pct + '%';
  var time = document.getElementById('sr-scb-time');
  if(time) time.textContent = srFormatTime(SR_CAPTURE.t * SR_CAPTURE.duration);
  // Rebuild ticks
  var ticks = document.getElementById('sr-scb-ticks');
  if(ticks){
    while(ticks.firstChild) ticks.removeChild(ticks.firstChild);
    var anomalies = (drone && drone.on) ? SR_DRONE_ANOMALIES : (base && base.on) ? SR_BASE_ANOMALIES : [];
    anomalies.forEach(function(a){
      var tick = document.createElement('div');
      tick.className = 'sr-scb-tick ' + a.severity;
      tick.style.left = (a.t * 100) + '%';
      tick.title = a.type + ' — ' + a.timestamp;
      tick.addEventListener('click', function(e){
        e.stopPropagation();
        SR_CAPTURE.t = a.t;
        SR_CAPTURE.playing = false;
        srUpdatePlayButton();
        srSceneRender();
      });
      ticks.appendChild(tick);
    });
  }
}





// ============================================================
// PHASE 3 — PROCESSING STAGE RENDERING
// Renders Geotagged Images (camera pins), Orthomosaic, DSM, DTM,
// 3D Model, Point Cloud. Heavy layers are mutually exclusive,
// with 350ms opacity crossfade between them.
// ============================================================

// Anomaly positions per Processing layer (synthesised — Loop FEATURES doesn't
// carry per-tile/per-cell anomaly geometry).
var SR_ORTHO_ANOMALIES = [
  { id:'O1', x:280, y:200, severity:'warn', type:'Low-sharpness tile',           detail:'Sharpness score 52 (target 60+)' },
  { id:'O2', x:580, y:180, severity:'warn', type:'Visible blending seam',         detail:'Edge detection found seam exceeding threshold' },
  { id:'O3', x:720, y:460, severity:'crit', type:'NoData region',                 detail:'Coverage gap of 1,200 m^2 - no contributing images' },
  { id:'O4', x:380, y:480, severity:'warn', type:'Insufficient image contribution', detail:'2 contributing images (threshold 3+)' }
];
var SR_DSM_ANOMALIES = [
  { id:'D1', x:320, y:220, severity:'warn', type:'Low height confidence', detail:'< 2 contributing images per cell across 340 m^2' },
  { id:'D2', x:600, y:380, severity:'crit', type:'Noise spike',           detail:'Elevation derivative > threshold; suspected vegetation' },
  { id:'D3', x:760, y:480, severity:'warn', type:'Low height confidence', detail:'< 2 contributing images per cell across 180 m^2' }
];
var SR_DTM_ANOMALIES = [
  { id:'T1', x:360, y:230, severity:'warn', type:'Over-interpolation',         detail:'Contiguous interpolated region 920 m^2' },
  { id:'T2', x:670, y:420, severity:'crit', type:'Severe over-interpolation', detail:'Contiguous interpolated region 1,400 m^2' },
  { id:'T3', x:200, y:490, severity:'warn', type:'Building footprint visible', detail:'Cells suggesting structure not removed by classifier' },
  { id:'T4', x:440, y:380, severity:'warn', type:'Vegetation contamination',   detail:'Cells classified as ground but elevation above local median + 50cm' }
];
var SR_MESH_ANOMALIES = [
  { id:'M1', x:340, y:280, severity:'crit', type:'Mesh hole',          detail:'Gap of 4.2 m^2 in geometry' },
  { id:'M2', x:620, y:200, severity:'warn', type:'Low texture quality', detail:'Per-face GSD 7.2 cm/px on 2.4 m^2 face' },
  { id:'M3', x:760, y:460, severity:'warn', type:'Floating geometry',   detail:'Disconnected mesh fragment 1.8 m^2' }
];
var SR_PC_ANOMALIES = [
  { id:'P1', x:380, y:240, severity:'warn', type:'Low point density', detail:'14 pts/m^2 (threshold 20 pts/m^2) across 280 m^2' },
  { id:'P2', x:660, y:420, severity:'crit', type:'Hole in coverage',  detail:'Contiguous 18 m^2 region with no points' },
  { id:'P3', x:220, y:480, severity:'warn', type:'Noise cluster',     detail:'High local variance suggesting scattered noise' }
];

// Image preview state
var SR_IMAGE_PREVIEW = { open:false, index:0, pins:null };

// ============================================================
// DEFS — Processing-specific gradients & patterns
// ============================================================
function srEnsureProcessingDefs(){
  var defs = document.querySelector('#sr-overlay defs');
  if(!defs || document.getElementById('sr-pr-orthoTex')) return;

  var ns = 'http://www.w3.org/2000/svg';

  // Ortho texture pattern (aerial-imagery look)
  var orthoTex = document.createElementNS(ns, 'pattern');
  orthoTex.setAttribute('id', 'sr-pr-orthoTex');
  orthoTex.setAttribute('x','0'); orthoTex.setAttribute('y','0');
  orthoTex.setAttribute('width','80'); orthoTex.setAttribute('height','80');
  orthoTex.setAttribute('patternUnits','userSpaceOnUse');
  orthoTex.innerHTML =
    '<rect width="80" height="80" fill="#5a6248"/>' +
    '<ellipse cx="20" cy="22" rx="14" ry="9" fill="#4d5a3e" opacity="0.6"/>' +
    '<ellipse cx="55" cy="35" rx="10" ry="6" fill="#675c44" opacity="0.5"/>' +
    '<ellipse cx="35" cy="55" rx="12" ry="8" fill="#4f5840" opacity="0.55"/>' +
    '<ellipse cx="68" cy="62" rx="8" ry="5" fill="#5e5946" opacity="0.45"/>' +
    '<rect x="12" y="40" width="22" height="3" fill="#3a4032" opacity="0.4"/>';
  defs.appendChild(orthoTex);

  // Elevation ramp
  var elevRamp = document.createElementNS(ns, 'linearGradient');
  elevRamp.setAttribute('id','sr-pr-elevRamp');
  elevRamp.setAttribute('x1','0%'); elevRamp.setAttribute('y1','0%');
  elevRamp.setAttribute('x2','0%'); elevRamp.setAttribute('y2','100%');
  elevRamp.innerHTML =
    '<stop offset="0%" stop-color="#7a5544"/>' +
    '<stop offset="35%" stop-color="#6e6a3e"/>' +
    '<stop offset="65%" stop-color="#4d6a4e"/>' +
    '<stop offset="100%" stop-color="#3a5060"/>';
  defs.appendChild(elevRamp);

  // DTM cross-hatch pattern
  var interpHatch = document.createElementNS(ns, 'pattern');
  interpHatch.setAttribute('id','sr-pr-interpHatch');
  interpHatch.setAttribute('patternUnits','userSpaceOnUse');
  interpHatch.setAttribute('width','6'); interpHatch.setAttribute('height','6');
  interpHatch.setAttribute('patternTransform','rotate(45)');
  interpHatch.innerHTML = '<line x1="0" y1="0" x2="0" y2="6" stroke="rgba(235, 242, 248, 0.20)" stroke-width="0.5"/>';
  defs.appendChild(interpHatch);

  // Mesh face gradients
  ['Light','Mid','Dark'].forEach(function(tone, idx){
    var grad = document.createElementNS(ns, 'linearGradient');
    grad.setAttribute('id','sr-pr-meshFace'+tone);
    grad.setAttribute('x1','0%'); grad.setAttribute('y1','0%');
    grad.setAttribute('x2','100%'); grad.setAttribute('y2','100%');
    var colors = [
      ['#5a6555','#3a4540'],
      ['#4a5550','#2a3535'],
      ['#3a4540','#202830']
    ][idx];
    grad.innerHTML =
      '<stop offset="0%" stop-color="'+colors[0]+'"/>' +
      '<stop offset="100%" stop-color="'+colors[1]+'"/>';
    defs.appendChild(grad);
  });

  // Score overlays — gradients used in Score mode quality tints
  ['scoreGood','#7CB89A','scoreWarn','#D2AA4E','scoreCrit','#C86262'].forEach(function(){});
  ['Good','Warn','Crit'].forEach(function(level){
    var color = level === 'Good' ? '#7CB89A' : level === 'Warn' ? '#D2AA4E' : '#C86262';
    var grad = document.createElementNS(ns, 'linearGradient');
    grad.setAttribute('id', 'sr-pr-score' + level);
    grad.setAttribute('x1','0%'); grad.setAttribute('y1','0%');
    grad.setAttribute('x2','100%'); grad.setAttribute('y2','100%');
    var startOp = level === 'Crit' ? '0.65' : '0.55';
    var endOp = level === 'Crit' ? '0.30' : '0.25';
    grad.innerHTML =
      '<stop offset="0%" stop-color="' + color + '" stop-opacity="' + startOp + '"/>' +
      '<stop offset="100%" stop-color="' + color + '" stop-opacity="' + endOp + '"/>';
    defs.appendChild(grad);
  });
}

// ============================================================
// PROCESSING HEAVY LAYER CONTAINERS
// All five heavy layers are mounted; we control opacity for the active one.
// Pre-built once; subsequent renders only update opacity + mode-specific overlays.
// ============================================================
var SR_PROCESSING_BUILT = false;

function srEnsureProcessingLayers(){
  if(SR_PROCESSING_BUILT) return;
  var stageGroup = document.getElementById('sr-scene-stage');
  if(!stageGroup) return;
  srEnsureProcessingDefs();

  // We add a persistent container for heavy layers OUTSIDE the dynamic stage group,
  // so that srSceneRender's clearGroup doesn't wipe them. Add to the SVG directly.
  var svg = document.getElementById('sr-overlay');
  if(!svg) return;
  if(document.getElementById('sr-pr-layers')) return;

  // Find the right insertion point — after sr-terrain, before sr-scene-stage
  var terrain = document.getElementById('sr-terrain');
  var sceneStage = document.getElementById('sr-scene-stage');

  var prLayers = srEl('g', {id:'sr-pr-layers'});
  // Each heavy layer is a group with class sr-pr-hvy. CSS controls opacity transitions.
  // Start all hidden.
  prLayers.innerHTML =
    '<g class="sr-pr-hvy" id="sr-pr-ortho"></g>' +
    '<g class="sr-pr-hvy" id="sr-pr-dsm"></g>' +
    '<g class="sr-pr-hvy" id="sr-pr-dtm"></g>' +
    '<g class="sr-pr-hvy" id="sr-pr-mesh"></g>' +
    '<g class="sr-pr-hvy" id="sr-pr-pcd"></g>';
  svg.insertBefore(prLayers, sceneStage);

  // Populate Orthomosaic
  var ortho = document.getElementById('sr-pr-ortho');
  ortho.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'url(#sr-pr-orthoTex)', rx:2,
    'data-layer':'ortho', 'data-feat':'ortho'
  }));
  ortho.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'none',
    stroke:'rgba(148, 212, 232, 0.2)', 'stroke-width':'0.5', rx:2
  }));

  // Populate DSM
  var dsm = document.getElementById('sr-pr-dsm');
  dsm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'url(#sr-pr-elevRamp)', rx:2,
    'data-layer':'dsm', 'data-feat':'dsm'
  }));
  dsm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'url(#sr-pr-meshFaceLight)', opacity:'0.25', rx:2
  }));
  // contour lines for hint of elevation
  [['380','320','120','60'], ['380','320','160','80'], ['620','240','80','40'], ['620','240','120','60']].forEach(function(c){
    dsm.appendChild(srEl('ellipse', {
      cx:c[0], cy:c[1], rx:c[2], ry:c[3],
      fill:'none', stroke:'rgba(2, 3, 8, 0.5)', 'stroke-width':'0.5', opacity:'0.25'
    }));
  });
  dsm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'none',
    stroke:'rgba(148, 212, 232, 0.2)', 'stroke-width':'0.5', rx:2
  }));

  // Populate DTM (DSM-like with hatch overlays)
  var dtm = document.getElementById('sr-pr-dtm');
  dtm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'url(#sr-pr-elevRamp)', rx:2,
    'data-layer':'dtm', 'data-feat':'dtm'
  }));
  dtm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'url(#sr-pr-meshFaceLight)', opacity:'0.25', rx:2
  }));
  // Interpolation regions with cross-hatch (always visible — Pass 2 DTM-1)
  [['280','180','180','100'], ['600','380','140','80'], ['150','450','100','80']].forEach(function(r){
    dtm.appendChild(srEl('rect', {
      x:r[0], y:r[1], width:r[2], height:r[3], fill:'url(#sr-pr-interpHatch)', rx:2
    }));
  });
  // Contours
  dtm.appendChild(srEl('ellipse', {
    cx:380, cy:320, rx:120, ry:60,
    fill:'none', stroke:'rgba(2, 3, 8, 0.5)', 'stroke-width':'0.5', opacity:'0.25'
  }));
  dtm.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'none',
    stroke:'rgba(148, 212, 232, 0.2)', 'stroke-width':'0.5', rx:2
  }));

  // Populate 3D Model (faceted polygon mesh)
  var mesh = document.getElementById('sr-pr-mesh');
  var faces = [
    {points:'120,300 380,80 660,140 880,80 880,300', fill:'url(#sr-pr-meshFaceDark)'},
    {points:'120,300 880,300 880,580 120,580',       fill:'url(#sr-pr-meshFaceMid)'},
    {points:'280,420 360,340 440,420 400,500 320,500', fill:'url(#sr-pr-meshFaceLight)'},
    {points:'580,440 660,380 740,440 720,520 600,520', fill:'url(#sr-pr-meshFaceLight)'},
    {points:'440,460 540,460 520,540 460,540',       fill:'url(#sr-pr-meshFaceDark)'}
  ];
  var meshFaces = srEl('g', {id:'sr-pr-mesh-faces', 'data-layer':'mesh', 'data-feat':'mesh', style:'cursor: pointer;'});
  faces.forEach(function(f){
    meshFaces.appendChild(srEl('polygon', {points:f.points, fill:f.fill}));
  });
  mesh.appendChild(meshFaces);

  // Mesh wireframe
  var wireLines = [
    'M 120 300 L 380 80 L 660 140 L 880 80 L 880 300',
    'M 280 420 L 360 340 L 440 420 M 360 340 L 400 500 M 280 420 L 320 500 L 400 500 M 440 420 L 400 500',
    'M 580 440 L 660 380 L 740 440 M 660 380 L 720 520 M 580 440 L 600 520 L 720 520 M 740 440 L 720 520',
    'M 440 460 L 540 460 L 520 540 L 460 540 Z',
    'M 150 350 L 250 380 L 200 450 Z M 750 340 L 820 380 L 780 440 Z M 820 460 L 870 500 L 830 540 Z'
  ];
  var wf = srEl('g', {stroke:'rgba(2, 3, 8, 0.4)', 'stroke-width':'0.4', fill:'none'});
  wireLines.forEach(function(d){
    wf.appendChild(srEl('path', {d:d}));
  });
  mesh.appendChild(wf);

  // Populate Point Cloud (~1200 individual points)
  var pcd = document.getElementById('sr-pr-pcd');
  pcd.appendChild(srEl('rect', {x:120, y:80, width:760, height:500, fill:'#0a0e15', rx:2,
    'data-layer':'pcd', 'data-feat':'pcd'}));
  var pcdPoints = srEl('g', {});
  for(var i=0; i<1200; i++){
    var x = 120 + Math.random() * 760;
    var y = 80 + Math.random() * 500;
    var densityRoll = Math.random();
    if(x > 600 && x < 730 && y > 380 && y < 460 && densityRoll > 0.15) continue; // hole
    if(x > 300 && x < 460 && y > 200 && y < 290 && densityRoll > 0.45) continue; // low density
    var intensity = 0.45 + Math.random() * 0.5;
    pcdPoints.appendChild(srEl('circle', {
      cx:x, cy:y, r: 0.9 + Math.random() * 0.5,
      fill: 'rgba(180, 195, 210, ' + intensity.toFixed(2) + ')'
    }));
  }
  pcd.appendChild(pcdPoints);
  pcd.appendChild(srEl('rect', {x:120, y:80, width:760, height:500, fill:'none',
    stroke:'rgba(148, 212, 232, 0.2)', 'stroke-width':'0.5', rx:2}));

  SR_PROCESSING_BUILT = true;
}

// Apply visibility and mode treatments to processing heavy layers
function srApplyProcessingState(){
  srEnsureProcessingLayers();
  ['ortho','dsm','dtm','mesh','pcd'].forEach(function(id){
    var el = document.getElementById('sr-pr-' + id);
    if(!el) return;
    var layer = srFindLayer(id);
    var visible = layer && layer.on;
    el.classList.toggle('active', !!visible);

    // Mode-specific filter for the active layer
    if(visible){
      if(SR.mode === 'anomalies'){
        el.style.filter = 'saturate(0.5)';
        el.style.opacity = '0.4';
      } else {
        el.style.filter = '';
        el.style.opacity = '1';
      }
    }
  });

  // Render mode-specific overlays + anomalies on the active heavy layer
  srRenderProcessingActiveLayerOverlays();
  // Active-layer pill bottom-left
  srUpdateActiveLayerPill();
}

// Active-layer pill ("ACTIVE LAYER · ORTHOMOSAIC")
function srUpdateActiveLayerPill(){
  var view = document.getElementById('view-sr');
  if(!view) return;
  var pill = document.getElementById('sr-pr-active-pill');
  var activeHeavy = null;
  ['ortho','dsm','dtm','mesh','pcd','cutfill'].forEach(function(id){
    var L = srFindLayer(id);
    if(L && L.on) activeHeavy = L;
  });
  if(!pill && activeHeavy){
    pill = document.createElement('div');
    pill.id = 'sr-pr-active-pill';
    pill.className = 'sr-pr-active-pill';
    view.appendChild(pill);
  }
  if(pill){
    if(activeHeavy){
      pill.innerHTML = '<span class="lbl">ACTIVE LAYER &middot;</span> <span class="val">' + activeHeavy.name.toUpperCase() + '</span>';
      pill.classList.add('show');
    } else {
      pill.classList.remove('show');
    }
  }
}

// Render mode-specific overlays and anomaly markers ON TOP of active heavy layer.
// Uses a dynamic group inside #sr-scene-stage so it clears on each render.
function srRenderProcessingActiveLayerOverlays(){
  var stageGroup = document.getElementById('sr-scene-stage');
  if(!stageGroup) return;

  // Determine active heavy layer
  var activeId = null;
  ['ortho','dsm','dtm','mesh','pcd'].forEach(function(id){
    var L = srFindLayer(id);
    if(L && L.on) activeId = id;
  });
  if(!activeId) return;

  var anomalies =
    activeId === 'ortho' ? SR_ORTHO_ANOMALIES :
    activeId === 'dsm'   ? SR_DSM_ANOMALIES :
    activeId === 'dtm'   ? SR_DTM_ANOMALIES :
    activeId === 'mesh'  ? SR_MESH_ANOMALIES :
    activeId === 'pcd'   ? SR_PC_ANOMALIES : [];

  // Score-mode overlay
  if(SR.mode === 'score'){
    var overlay = srEl('g', {});
    if(activeId === 'ortho') srRenderOrthoScore(overlay);
    else if(activeId === 'dsm') srRenderDsmScore(overlay);
    else if(activeId === 'dtm') srRenderDtmScore(overlay);
    else if(activeId === 'mesh') srRenderMeshScore(overlay);
    else if(activeId === 'pcd') srRenderPcScore(overlay);
    stageGroup.appendChild(overlay);
  }

  // Anomaly markers (visible in default and anomalies modes)
  if(SR.mode !== 'score'){
    anomalies.forEach(function(a){
      var marker = srEl('g', {
        transform: 'translate('+a.x+','+a.y+')',
        opacity: SR.mode === 'anomalies' ? '1' : '0.5',
        style: 'cursor: pointer;',
        'data-layer': activeId, 'data-feat': activeId
      });
      marker.appendChild(srEl('circle', {r:5, fill:'rgba(2, 3, 8, 0.6)', stroke:'white', 'stroke-width':'0.8'}));
      marker.appendChild(srEl('circle', {r:3.5, fill: a.severity === 'crit' ? '#C86262' : '#D2AA4E'}));
      stageGroup.appendChild(marker);
    });
  }
}

function srRenderOrthoScore(g){
  var tiles = [
    {x:120, y:80,  w:200, h:150, score:'good'},
    {x:320, y:80,  w:200, h:150, score:'good'},
    {x:520, y:80,  w:200, h:150, score:'warn'},
    {x:720, y:80,  w:160, h:150, score:'good'},
    {x:120, y:230, w:200, h:150, score:'warn'},
    {x:320, y:230, w:200, h:150, score:'good'},
    {x:520, y:230, w:200, h:150, score:'good'},
    {x:720, y:230, w:160, h:150, score:'good'},
    {x:120, y:380, w:200, h:200, score:'good'},
    {x:320, y:380, w:200, h:200, score:'warn'},
    {x:520, y:380, w:200, h:200, score:'good'},
    {x:720, y:380, w:160, h:200, score:'crit'}
  ];
  tiles.forEach(function(t){
    g.appendChild(srEl('rect', {
      x:t.x, y:t.y, width:t.w, height:t.h,
      fill: t.score === 'good' ? 'url(#sr-pr-scoreGood)' : t.score === 'warn' ? 'url(#sr-pr-scoreWarn)' : 'url(#sr-pr-scoreCrit)',
      opacity:'0.5'
    }));
  });
}

function srRenderDsmScore(g){
  for(var x=120; x<880; x+=40){
    for(var y=80; y<580; y+=40){
      var d1 = Math.hypot(x-320, y-220);
      var d2 = Math.hypot(x-600, y-380);
      var d3 = Math.hypot(x-760, y-480);
      var score = 'good';
      if(d1 < 50 || d3 < 50) score = 'warn';
      if(d2 < 40) score = 'crit';
      if(score === 'good') continue;
      g.appendChild(srEl('rect', {
        x:x, y:y, width:40, height:40,
        fill: score === 'warn' ? 'url(#sr-pr-scoreWarn)' : 'url(#sr-pr-scoreCrit)',
        opacity:'0.55'
      }));
    }
  }
}

function srRenderDtmScore(g){
  var regions = [
    {x:280, y:180, w:180, h:100, score:'warn'},
    {x:600, y:380, w:140, h:80,  score:'crit'},
    {x:150, y:450, w:100, h:80,  score:'warn'},
    {x:440, y:380, w:80,  h:60,  score:'warn'}
  ];
  regions.forEach(function(r){
    g.appendChild(srEl('rect', {
      x:r.x, y:r.y, width:r.w, height:r.h,
      fill: r.score === 'warn' ? 'url(#sr-pr-scoreWarn)' : 'url(#sr-pr-scoreCrit)',
      opacity:'0.55'
    }));
  });
}

function srRenderMeshScore(g){
  var overlays = [
    {points:'280,420 360,340 440,420 400,500 320,500', fill:'url(#sr-pr-scoreWarn)'},
    {points:'120,300 880,300 880,580 120,580',         fill:'url(#sr-pr-scoreGood)'}
  ];
  overlays.forEach(function(o){
    g.appendChild(srEl('polygon', {points:o.points, fill:o.fill, opacity:'0.5'}));
  });
}

function srRenderPcScore(g){
  g.appendChild(srEl('ellipse', {cx:380, cy:240, rx:70, ry:45, fill:'url(#sr-pr-scoreWarn)', opacity:'0.5'}));
  g.appendChild(srEl('ellipse', {cx:660, cy:420, rx:50, ry:35, fill:'url(#sr-pr-scoreCrit)', opacity:'0.55'}));
}

// ============================================================
// GEOTAGGED IMAGES — camera pins
// ============================================================
function srRenderImages(stageGroup){
  var imgs = FEATURES.images || [];
  // Sub-sample: every 6th image for performance/clarity
  var subset = [];
  for(var i=0; i<imgs.length; i+=6){
    var pin = imgs[i];
    // Synthesise per-pin metadata (existing FEATURES.images is just [{x,y}])
    var sharpness = 60 + Math.round((Math.sin(i*0.7) + Math.cos(i*1.3)) * 18);
    var isWarn = (i % 11 === 3);
    var isCrit = (i === 18);
    subset.push({
      id: 'IMG_' + String(58 + i*9).padStart(4, '0'),
      x: srNX(pin.x), y: srNY(pin.y),
      sharpness: isCrit ? 38 : (isWarn ? 48 : sharpness),
      severity: isCrit ? 'crit' : (isWarn ? 'warn' : null),
      time: '14:' + String(23 + Math.floor(i*0.3)).padStart(2,'0') + ':' + String(i%60).padStart(2,'0')
    });
  }
  SR_IMAGE_PREVIEW.pins = subset;

  subset.forEach(function(img, idx){
    var pinG = srEl('g', {
      transform: 'translate('+img.x+','+img.y+')',
      style: 'cursor: pointer;',
      'data-img-idx': idx
    });
    pinG.appendChild(srEl('circle', {
      r:6,
      fill:'rgba(2, 3, 8, 0.65)',
      stroke: img.severity === 'crit' ? '#C86262' : img.severity === 'warn' ? '#D2AA4E' : 'rgba(148, 212, 232, 0.7)',
      'stroke-width':'0.8'
    }));
    pinG.appendChild(srEl('circle', {
      r:2.5,
      fill: img.severity === 'crit' ? '#C86262' : img.severity === 'warn' ? '#D2AA4E' : '#94D4E8'
    }));
    pinG.appendChild(srEl('polygon', {
      points:'0,-9 -2,-6 2,-6',
      fill: img.severity === 'crit' ? '#C86262' : img.severity === 'warn' ? '#D2AA4E' : 'rgba(148, 212, 232, 0.7)'
    }));
    pinG.addEventListener('click', function(e){
      e.stopPropagation();
      srOpenImagePreview(idx);
    });
    stageGroup.appendChild(pinG);
  });
}

// ============================================================
// IMAGE PREVIEW OVERLAY
// ============================================================
function srEnsureImagePreview(){
  var existing = document.getElementById('sr-img-preview');
  if(existing) return existing;
  var view = document.getElementById('view-sr');
  if(!view) return null;

  var ov = document.createElement('div');
  ov.id = 'sr-img-preview';
  ov.className = 'sr-img-preview';
  ov.innerHTML =
    '<div class="sr-imgp-panel">' +
      '<div class="sr-imgp-head">' +
        '<div class="sr-imgp-id" id="sr-imgp-id">IMG_0000</div>' +
        '<button class="sr-imgp-close" id="sr-imgp-close">' +
          '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3L11 11M11 3L3 11" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>' +
        '</button>' +
      '</div>' +
      '<div class="sr-imgp-canvas"></div>' +
      '<div class="sr-imgp-meta">' +
        '<div><div class="lbl">Capture time</div><div id="sr-imgp-time">14:26:23</div></div>' +
        '<div><div class="lbl">Altitude</div><div id="sr-imgp-alt">85 m AGL</div></div>' +
        '<div><div class="lbl">Sharpness</div><div id="sr-imgp-sharp">87</div></div>' +
      '</div>' +
      '<div class="sr-imgp-nav">' +
        '<button class="sr-imgp-navbtn" id="sr-imgp-prev">&larr; PREV</button>' +
        '<button class="sr-imgp-navbtn" id="sr-imgp-next">NEXT &rarr;</button>' +
      '</div>' +
    '</div>';
  view.appendChild(ov);

  document.getElementById('sr-imgp-close').addEventListener('click', srCloseImagePreview);
  ov.addEventListener('click', function(e){ if(e.target === ov) srCloseImagePreview(); });
  document.getElementById('sr-imgp-prev').addEventListener('click', function(){
    if(SR_IMAGE_PREVIEW.index > 0){ SR_IMAGE_PREVIEW.index--; srUpdateImagePreview(); }
  });
  document.getElementById('sr-imgp-next').addEventListener('click', function(){
    if(SR_IMAGE_PREVIEW.pins && SR_IMAGE_PREVIEW.index < SR_IMAGE_PREVIEW.pins.length - 1){
      SR_IMAGE_PREVIEW.index++;
      srUpdateImagePreview();
    }
  });
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape' && SR_IMAGE_PREVIEW.open) srCloseImagePreview();
  });
  return ov;
}

function srOpenImagePreview(idx){
  srEnsureImagePreview();
  SR_IMAGE_PREVIEW.index = idx;
  SR_IMAGE_PREVIEW.open = true;
  srUpdateImagePreview();
  document.getElementById('sr-img-preview').classList.add('show');
}
function srCloseImagePreview(){
  SR_IMAGE_PREVIEW.open = false;
  var ov = document.getElementById('sr-img-preview');
  if(ov) ov.classList.remove('show');
}
function srUpdateImagePreview(){
  if(!SR_IMAGE_PREVIEW.pins) return;
  var img = SR_IMAGE_PREVIEW.pins[SR_IMAGE_PREVIEW.index];
  if(!img) return;
  document.getElementById('sr-imgp-id').textContent = img.id;
  document.getElementById('sr-imgp-time').textContent = img.time;
  document.getElementById('sr-imgp-alt').textContent = '85 m AGL';
  document.getElementById('sr-imgp-sharp').textContent = img.sharpness;
  document.getElementById('sr-imgp-prev').disabled = SR_IMAGE_PREVIEW.index === 0;
  document.getElementById('sr-imgp-next').disabled = SR_IMAGE_PREVIEW.index === SR_IMAGE_PREVIEW.pins.length - 1;
}

// ============================================================
// PROCESSING STAGE RENDER ENTRY
// ============================================================
function srRenderProcessingStage(stageGroup){
  srEnsureProcessingLayers();   // builds persistent heavy-layer groups if needed
  srApplyProcessingState();     // updates active/inactive opacity + overlays

  // Render Geotagged Images pins (lightweight, non-heavy) into the dynamic stage group
  var images = srFindLayer('images');
  if(images && images.on){
    srRenderImages(stageGroup);
  }
}


// ============================================================
// PHASE 4 — ANALYTICS STAGE RENDERING
// Polygon-based objects (Stockpiles, Pits, Waste Dumps) with object-
// identifier colours, headline labels, cluster-priority labelling.
// Cut-Fill is a continuous-surface heavy layer with bidirectional
// cut (blue) / fill (warm red) cells and a histogram legend.
// ============================================================

// Object-identifier colours per Pass 3 shared opener
var SR_AN_COLORS = {
  stockpiles: '#D2AA4E',  // gold tint
  pits:       '#C86262',  // red tint
  dumps:      '#94D4E8'   // steel cyan
};

// Cut-Fill cells — synthesised raster covering the site
var SR_CF_CELLS = null;
function srBuildCutFillCells(){
  if(SR_CF_CELLS) return;
  SR_CF_CELLS = [];
  var cellSize = 16;
  // Centres of polygons in the existing FEATURES data, expressed in scene coords
  var stockpileCentres = (FEATURES.stockpiles || []).map(function(sp){
    var cx = 0, cy = 0;
    sp.pts.forEach(function(p){ cx += srNX(p[0]); cy += srNY(p[1]); });
    return [cx / sp.pts.length, cy / sp.pts.length];
  });
  var pitCentres = (FEATURES.pits || []).map(function(p){
    var cx = 0, cy = 0;
    p.pts.forEach(function(pt){ cx += srNX(pt[0]); cy += srNY(pt[1]); });
    return [cx / p.pts.length, cy / p.pts.length];
  });
  var dumpCentres = (FEATURES.dumps || []).map(function(d){
    var cx = 0, cy = 0;
    d.pts.forEach(function(pt){ cx += srNX(pt[0]); cy += srNY(pt[1]); });
    return [cx / d.pts.length, cy / d.pts.length];
  });

  for(var x = 120; x < 880; x += cellSize){
    for(var y = 80; y < 580; y += cellSize){
      var value = 0;

      // Pits are net CUT (current below reference)
      pitCentres.forEach(function(c){
        var d = Math.hypot(x - c[0], y - c[1]);
        if(d < 90) value = Math.min(value, -Math.max(0, 7 - d / 14));
      });

      // Stockpiles are net FILL (current above reference)
      stockpileCentres.forEach(function(c){
        var d = Math.hypot(x - c[0], y - c[1]);
        if(d < 50) value = Math.max(value, 4 - d / 12);
      });

      // Waste Dumps are net FILL
      dumpCentres.forEach(function(c){
        var d = Math.hypot(x - c[0], y - c[1]);
        if(d < 50) value = Math.max(value, 5 - d / 10);
      });

      // Small noise so it doesn't look gridded
      value += (Math.random() - 0.5) * 0.3;

      SR_CF_CELLS.push({ x:x, y:y, w:cellSize, h:cellSize, value:value });
    }
  }
}

// Cut-Fill histogram bins (bidirectional around zero)
var SR_CF_HIST = [
  { value:-8, count:2,   type:'cut' },
  { value:-6, count:5,   type:'cut' },
  { value:-4, count:12,  type:'cut' },
  { value:-2, count:28,  type:'cut' },
  { value:-1, count:42,  type:'cut' },
  { value: 0, count:180, type:'atgrade' },
  { value: 1, count:38,  type:'fill' },
  { value: 2, count:24,  type:'fill' },
  { value: 4, count:14,  type:'fill' },
  { value: 6, count:4,   type:'fill' }
];

// ============================================================
// CUT-FILL HEAVY LAYER (continuous surface) — pre-mounted like Processing
// ============================================================
var SR_CF_BUILT = false;
function srEnsureCutFillLayer(){
  if(SR_CF_BUILT) return;
  var svg = document.getElementById('sr-overlay');
  if(!svg) return;
  if(document.getElementById('sr-an-cutfill')) return;

  srBuildCutFillCells();

  // Mount the cutfill heavy layer alongside the Processing heavy layers
  var prLayers = document.getElementById('sr-pr-layers');
  if(!prLayers) {
    // If Processing layers aren't mounted yet (Phase 3 ran first), this is unexpected
    // but safe to handle: create a sibling group
    var sceneStage = document.getElementById('sr-scene-stage');
    var newG = srEl('g', {id:'sr-an-layers'});
    svg.insertBefore(newG, sceneStage);
    prLayers = newG;
  }

  var cf = srEl('g', {'class':'sr-pr-hvy', id:'sr-an-cutfill'});
  // Background — slightly darker than terrain to separate the cell field
  cf.appendChild(srEl('rect', {x:120, y:80, width:760, height:500, fill:'#10141d', rx:2}));

  // Cells
  var cellsG = srEl('g', {});
  SR_CF_CELLS.forEach(function(cell){
    var absVal = Math.abs(cell.value);
    var fillColor, opacity;
    if(absVal < 0.05){
      // At-grade
      fillColor = '#1f2632';
      opacity = 0.6;
    } else if(cell.value < 0){
      // Cut — blue-cyan
      var intensity = Math.min(absVal / 8, 1);
      var r = Math.round(74 - intensity * 24);
      var g = Math.round(112 - intensity * 24);
      var b = Math.round(136 + intensity * 16);
      fillColor = 'rgb(' + r + ',' + g + ',' + b + ')';
      opacity = 0.55 + intensity * 0.35;
    } else {
      // Fill — warm red
      var intensity2 = Math.min(cell.value / 6, 1);
      var r2 = Math.round(168 + intensity2 * 36);
      var g2 = Math.round(106 - intensity2 * 26);
      var b2 = Math.round(75 - intensity2 * 27);
      fillColor = 'rgb(' + r2 + ',' + g2 + ',' + b2 + ')';
      opacity = 0.55 + intensity2 * 0.35;
    }
    cellsG.appendChild(srEl('rect', {
      x:cell.x, y:cell.y, width:cell.w, height:cell.h,
      fill:fillColor, 'fill-opacity':opacity
    }));
  });
  cf.appendChild(cellsG);

  // Border — clickable for cutfill anomaly card
  cf.appendChild(srEl('rect', {
    x:120, y:80, width:760, height:500, fill:'none',
    stroke:'rgba(148, 212, 232, 0.2)', 'stroke-width':'0.5', rx:2,
    'data-layer':'cutfill', 'data-feat':'CF-B3'
  }));

  prLayers.appendChild(cf);
  SR_CF_BUILT = true;
}

// ============================================================
// CUT-FILL HISTOGRAM LEGEND (DOM, not SVG) — bottom of scene
// ============================================================
function srEnsureCutFillHistogram(){
  var existing = document.getElementById('sr-cf-histogram');
  if(existing) return existing;
  var view = document.getElementById('view-sr');
  if(!view) return null;
  var h = document.createElement('div');
  h.id = 'sr-cf-histogram';
  h.className = 'sr-cf-histogram';
  h.innerHTML =
    '<div class="sr-cf-h-title">Cut &middot; Fill distribution</div>' +
    '<div class="sr-cf-h-bars" id="sr-cf-h-bars"></div>' +
    '<div class="sr-cf-h-scale"><span>-8 m</span><span>0</span><span>+6 m</span></div>' +
    '<div class="sr-cf-h-summary">' +
      '<span class="cut">CUT 14,200 m^3</span>' +
      '<span class="net">NET +3,400 m^3</span>' +
      '<span class="fill">FILL 17,600 m^3</span>' +
    '</div>';
  view.appendChild(h);

  // Bars
  var barsContainer = document.getElementById('sr-cf-h-bars');
  var maxCount = Math.max.apply(null, SR_CF_HIST.map(function(b){ return b.count; }));
  SR_CF_HIST.forEach(function(bin){
    var bar = document.createElement('div');
    bar.className = 'sr-cf-h-bar';
    bar.style.height = (bin.count / maxCount * 100) + '%';
    if(bin.type === 'cut'){
      bar.style.background = 'rgba(74, 112, 136, ' + (0.5 + Math.abs(bin.value) / 16).toFixed(2) + ')';
    } else if(bin.type === 'fill'){
      bar.style.background = 'rgba(200, 138, 107, ' + (0.5 + bin.value / 12).toFixed(2) + ')';
    } else {
      bar.style.background = 'rgba(200, 215, 228, 0.25)';
    }
    barsContainer.appendChild(bar);
  });

  return h;
}

function srUpdateCutFillHistogramVisibility(){
  var h = document.getElementById('sr-cf-histogram');
  if(!h) return;
  var cf = srFindLayer('cutfill');
  h.classList.toggle('show', !!(cf && cf.on));
}

// ============================================================
// POLYGON RENDERING (Stockpiles / Pits / Waste Dumps)
// ============================================================
function srComputeCentroid(pts){
  var cx = 0, cy = 0;
  pts.forEach(function(p){ cx += srNX(p[0]); cy += srNY(p[1]); });
  return { x: cx / pts.length, y: cy / pts.length };
}

function srPolygonPointsAttr(pts){
  return pts.map(function(p){ return srNX(p[0]) + ',' + srNY(p[1]); }).join(' ');
}

function srAnPolygonState(f){
  if(typeof f.score === 'number' && f.score < 70) return 'crit';
  if(f.anomaly) return 'warn';
  if(typeof f.score === 'number' && f.score < 85) return 'warn';
  return 'good';
}

// Render one Analytics multi-layer (stockpiles, pits, or dumps)
function srRenderAnalyticsMulti(stageGroup, layerId){
  var layer = srFindLayer(layerId);
  if(!layer || !layer.on) return [];

  var visibleMap = SR_LAYER_UI.childVisible[layerId] || {};
  var feats = (FEATURES[layerId] || []).filter(function(f){ return visibleMap[f.id]; });
  if(!feats.length) return [];

  var identColor = SR_AN_COLORS[layerId];
  var cutfillVisible = !!(srFindLayer('cutfill') && srFindLayer('cutfill').on);

  // Collect items rendered for cluster-priority labelling
  var rendered = [];

  feats.forEach(function(f){
    var state = srAnPolygonState(f);
    var isFocused = !!(SR.selected && SR.selected.layerId === layerId && SR.selected.featId === f.id);

    var fillColor = identColor;
    var fillOpacity = 0.18;
    var strokeColor = identColor;
    var strokeOpacity = 0.65;

    if(SR.mode === 'score'){
      var stateCol = state === 'good' ? '#7CB89A' : state === 'warn' ? '#D2AA4E' : '#C86262';
      fillColor = stateCol;
      strokeColor = stateCol;
      fillOpacity = 0.25;
    }

    // Suppression: when Cut-Fill is underneath, suppress polygon interior fills
    if(cutfillVisible) fillOpacity = 0;

    // Anomalies-mode dimming for passing polygons
    var groupOpacity = 1;
    if(SR.mode === 'anomalies' && state === 'good') groupOpacity = 0.35;

    var pG = srEl('g', {
      opacity: groupOpacity,
      'data-layer': layerId, 'data-feat': f.id,
      style: 'cursor: pointer;'
    });

    var poly = srEl('polygon', {
      points: srPolygonPointsAttr(f.pts),
      fill: fillColor, 'fill-opacity': fillOpacity,
      stroke: strokeColor, 'stroke-opacity': strokeOpacity,
      'stroke-width': isFocused ? '2.5' : '1.5'
    });
    pG.appendChild(poly);

    // Pit-specific: toe contour (always visible at .35 opacity per Pass 3 Pit-1)
    if(layerId === 'pits'){
      var centroid = srComputeCentroid(f.pts);
      var shrunkPts = f.pts.map(function(p){
        var px = srNX(p[0]), py = srNY(p[1]);
        var dx = px - centroid.x, dy = py - centroid.y;
        return (centroid.x + dx * 0.55) + ',' + (centroid.y + dy * 0.55);
      }).join(' ');
      pG.appendChild(srEl('polygon', {
        points: shrunkPts,
        fill: 'none',
        stroke: identColor, 'stroke-opacity': '0.35',
        'stroke-width': '0.8', 'stroke-dasharray': '3 2'
      }));
    }

    // Anomaly marker for failing polygons
    if(state !== 'good'){
      var c = srComputeCentroid(f.pts);
      var marker = srEl('g', {
        transform: 'translate(' + c.x + ',' + (c.y - 4) + ')',
        opacity: SR.mode === 'anomalies' ? '1' : '0.5',
        'pointer-events': 'none'
      });
      marker.appendChild(srEl('circle', {r:5, fill:'rgba(2, 3, 8, 0.6)', stroke:'white', 'stroke-width':'0.8'}));
      marker.appendChild(srEl('circle', {r:3.5, fill: state === 'crit' ? '#C86262' : '#D2AA4E'}));
      pG.appendChild(marker);
    }

    stageGroup.appendChild(pG);

    // Track this feature for label rendering pass
    var sizeMetric =
      typeof f.vol === 'number' ? f.vol :
      typeof f.depth === 'number' ? f.depth * 50 :
      typeof f.delta === 'number' ? Math.abs(f.delta) * 100 : 0;
    rendered.push({
      layerId: layerId, feat: f,
      labelValue: f.vol || f.depth || f.delta,
      labelUnit: layerId === 'pits' ? 'M DEPTH' : 'M^3',
      sizeMetric: sizeMetric,
      state: state, isFocused: isFocused
    });
  });

  return rendered;
}

// ============================================================
// CLUSTER-PRIORITY LABELLING (Layer 2 §3.2.4)
// Focused → top-N by size → others as small dots-with-hover
// ============================================================
function srRenderAnalyticsLabels(stageGroup, allRendered){
  if(!allRendered.length) return;

  // Determine label set
  var focused = allRendered.filter(function(r){ return r.isFocused; });
  var nonFocused = allRendered.filter(function(r){ return !r.isFocused; });
  nonFocused.sort(function(a,b){ return b.sizeMetric - a.sizeMetric; });

  var topN = 5;
  var labelSlots = focused.length + topN;
  var labelled = focused.concat(nonFocused.slice(0, topN));
  var dotted = nonFocused.slice(topN);

  labelled.forEach(function(r){ srRenderAnLabel(stageGroup, r); });
  dotted.forEach(function(r){ srRenderAnDot(stageGroup, r); });
}

function srRenderAnLabel(parent, r){
  var c = srComputeCentroid(r.feat.pts);
  var labelY = c.y - 32;

  // Leader line
  parent.appendChild(srEl('line', {
    x1: c.x, y1: c.y, x2: c.x, y2: labelY + 10,
    stroke:'rgba(200, 215, 228, 0.30)', 'stroke-width':'0.5'
  }));

  var g = srEl('g', {
    transform: 'translate(' + c.x + ',' + labelY + ')',
    opacity: (SR.mode === 'anomalies' && r.state === 'good') ? 0.5 : 1
  });
  // State dot in Score mode
  var stateColor = null;
  if(SR.mode === 'score'){
    stateColor = r.state === 'good' ? '#7CB89A' : r.state === 'warn' ? '#D2AA4E' : '#C86262';
  }

  var displayVal = (typeof r.labelValue === 'number') ? r.labelValue.toLocaleString() : String(r.labelValue || '');
  var labelWidth = Math.max(72, displayVal.length * 9 + 36);

  g.appendChild(srEl('rect', {
    x: -labelWidth/2, y: -12, width: labelWidth, height: 22,
    rx: 3, ry: 3,
    fill:'rgba(2, 3, 8, 0.85)', stroke:'rgba(200, 215, 228, 0.18)', 'stroke-width':'0.5'
  }));

  if(stateColor){
    g.appendChild(srEl('circle', {
      cx: -labelWidth/2 + 8, cy: 0, r: 2.5, fill: stateColor
    }));
  }

  var text = srEl('text', {
    x: stateColor ? 4 : 0, y: 1, 'text-anchor':'middle', 'dominant-baseline':'middle',
    fill: 'rgba(235, 242, 248, 1)',
    'font-family':'Barlow', 'font-weight':'700', 'font-size':'13'
  });
  text.textContent = displayVal;
  g.appendChild(text);

  var unitText = srEl('text', {
    x: stateColor ? 4 : 0, y: 14, 'text-anchor':'middle', 'dominant-baseline':'middle',
    fill:'rgba(235, 242, 248, 0.48)',
    'font-family':'IBM Plex Mono', 'font-size':'8', 'letter-spacing':'.14em'
  });
  unitText.textContent = r.labelUnit;
  g.appendChild(unitText);

  parent.appendChild(g);
}

function srRenderAnDot(parent, r){
  var c = srComputeCentroid(r.feat.pts);
  var dot = srEl('circle', {
    cx: c.x, cy: c.y, r: 2.5,
    fill:'rgba(235, 242, 248, 0.45)',
    style:'cursor: pointer;',
    'data-layer': r.layerId, 'data-feat': r.feat.id
  });
  parent.appendChild(dot);
}

// ============================================================
// CUT-FILL APPLY (heavy-layer state for mutual exclusion)
// ============================================================
function srApplyCutFillState(){
  srEnsureCutFillLayer();
  srEnsureCutFillHistogram();
  var cf = srFindLayer('cutfill');
  var el = document.getElementById('sr-an-cutfill');
  if(el){
    var visible = cf && cf.on;
    el.classList.toggle('active', !!visible);
    if(visible && SR.mode === 'anomalies'){
      el.style.filter = 'saturate(0.5)';
      el.style.opacity = '0.5';
    } else if(visible){
      el.style.filter = '';
      el.style.opacity = '1';
    }
  }
  srUpdateCutFillHistogramVisibility();
}

// ============================================================
// ANALYTICS STAGE RENDER ENTRY
// ============================================================
function srRenderAnalyticsStage(stageGroup){
  // Cut-Fill heavy layer (mutual exclusion already enforced by layer panel)
  srApplyCutFillState();

  // Polygon rendering for the three multi-layers
  var allRendered = [];
  ['stockpiles','pits','dumps'].forEach(function(id){
    var rendered = srRenderAnalyticsMulti(stageGroup, id);
    allRendered = allRendered.concat(rendered);
  });

  // Cluster-priority labelling across all polygon objects
  srRenderAnalyticsLabels(stageGroup, allRendered);
}


// ============================================================
// NARRATIVE INSIGHTS PANEL — sentence-first structure
// Pattern: Name + Score → English statement → Details (collapsed)
// The statement: "X depends on a, b, c. {state}. Recommend {rec}."
// ============================================================

// Per-layer factor names ("a, b, c") — what each object depends on
var SR_NARRATIVE_FACTORS = {
  flight:    ['waypoint adherence', 'coverage', 'altitude consistency', 'overlap'],
  drone:     ['battery', 'flight stability', 'telemetry quality', 'sensor health'],
  base:      ['RTK fix quality', 'satellite count', 'horizontal RMS', 'vertical RMS'],
  gcps:      ['residual accuracy', 'threshold compliance', 'spatial coverage'],
  images:    ['frame count', 'geotag completeness', 'ground sample distance', 'sun angle'],
  ortho:     ['resolution', 'coverage', 'seamline quality', 'output size'],
  dsm:       ['grid resolution', 'vertical RMSE', 'horizontal RMSE', 'coverage completeness'],
  dtm:       ['grid resolution', 'classification confidence', 'ground percentage', 'vertical RMSE'],
  mesh:      ['triangle count', 'texture resolution', 'tie quality', 'output format'],
  pcd:       ['point count', 'point density', 'void coverage', 'classification'],
  stockpiles:['volume measurement', 'grade classification', 'AI confidence'],
  pits:      ['volume excavated', 'depth measurement', 'plan compliance'],
  dumps:     ['volume', 'boundary certainty', 'classification status'],
  cutfill:   ['cut volume', 'fill volume', 'reference surface confidence', 'change-detection sigma']
};

// Score thresholds → phrasing
function srScorePhrase(score){
  if(typeof score !== 'number') return null;
  if(score >= 90) return 'strong';
  if(score >= 70) return 'acceptable';
  return 'low';
}

// Score → state ('good' | 'warn' | 'crit')
function srScoreState(score){
  if(typeof score !== 'number') return 'good';
  if(score < 70) return 'crit';
  if(score < 85) return 'warn';
  return 'good';
}

// List-joining helper: "a, b, and c"
function srJoinList(items){
  if(!items || !items.length) return '';
  if(items.length === 1) return items[0];
  if(items.length === 2) return items[0] + ' and ' + items[1];
  return items.slice(0, -1).join(', ') + ', and ' + items[items.length - 1];
}

// Capitalise first letter
function srCap(s){
  if(!s) return '';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Lowercase first letter (for grammatical flow when inlining)
function srLow(s){
  if(!s) return '';
  return s.charAt(0).toLowerCase() + s.slice(1);
}

// ============================================================
// BUILD NARRATIVE — returns { sentence, state, hasIssue }
// ============================================================
function srBuildNarrative(layerId, det){
  if(!det) return { sentence:'', state:'good', hasIssue:false };

  var factors = SR_NARRATIVE_FACTORS[layerId] || [];
  var factorList = factors.length ? srJoinList(factors) : 'multiple factors';

  // Extract score if present in KPIs as ['Score', '94 / 100']
  var scoreVal = null;
  if(Array.isArray(det.kpis)){
    det.kpis.forEach(function(k){
      if(k && k[0] === 'Score' && typeof k[1] === 'string'){
        var m = k[1].match(/(\d+)/);
        if(m) scoreVal = parseInt(m[1], 10);
      }
    });
  }
  var state = srScoreState(scoreVal);

  // Determine issue list (anomalies and alerts)
  var issues = [];
  if(Array.isArray(det.anom)) det.anom.forEach(function(a){ if(a) issues.push(a); });
  if(Array.isArray(det.alerts)) det.alerts.forEach(function(a){ if(a) issues.push(a); });
  var hasIssue = issues.length > 0;

  // Get primary recommendation
  var rec = null;
  if(Array.isArray(det.recs) && det.recs.length){
    rec = det.recs[0];
  }

  // Build the entity name for "X" — use det.tag (or layerId fallback). Prefer the
  // detail's tag because it's more specific (e.g. "Control Point" vs "Control Points", "Stockpile" vs aggregate).
  var entity = det.tag || layerId;

  // Compose the state sentence
  var stateClause;
  if(hasIssue){
    // Negative path: "X depends on a, b, c. {issue evidence}. Recommend {rec}."
    var issueText = issues[0];
    // Try to identify which factor is implicated, by keyword search in the issue text
    var implicated = factors.find(function(f){
      // crude keyword match
      var words = f.toLowerCase().split(/\s+/);
      var t = issueText.toLowerCase();
      return words.some(function(w){ return w.length > 4 && t.indexOf(w) >= 0; });
    });
    if(implicated){
      stateClause = srCap(implicated) + ' is showing an issue: ' + srLow(issueText);
    } else {
      stateClause = srCap(issueText);
    }
  } else {
    // Positive path — all good or no issues raised
    if(scoreVal !== null){
      var phrase = srScorePhrase(scoreVal);
      if(phrase === 'strong'){
        stateClause = factors.length
          ? 'All ' + (factors.length === 1 ? 'one factor' : (factors.length + ' factors')) + ' are strong (score ' + scoreVal + '/100)'
          : 'Score ' + scoreVal + '/100 is strong';
      } else if(phrase === 'acceptable'){
        stateClause = 'All factors are within thresholds (score ' + scoreVal + '/100)';
      } else {
        stateClause = 'Score is low (' + scoreVal + '/100)';
      }
    } else {
      stateClause = 'All factors are within thresholds';
    }
  }

  // Recommend clause
  var recClause;
  if(rec){
    // Use existing rec verbatim. Prepend "Recommend" naturally.
    // If the rec already begins with a verb like "Ready", "Cleared", "Investigate", flow as "Recommend {rec}".
    // If it begins with a noun phrase, the same prefix works.
    recClause = 'Recommend ' + srLow(rec);
    // Strip a trailing period if doubled
    if(recClause.endsWith('..')) recClause = recClause.slice(0, -1);
  } else if(hasIssue){
    recClause = 'Recommend review before proceeding';
  } else {
    recClause = 'Recommend cleared for delivery';
  }

  // Combine
  var sentence = srCap(entity) + ' depends on ' + factorList + '. ' + stateClause + '. ' + recClause + '.';
  // Clean double periods
  sentence = sentence.replace(/\.\.+/g, '.');
  return { sentence: sentence, state: state, hasIssue: hasIssue, scoreVal: scoreVal };
}

// ============================================================
// AGGREGATE NARRATIVE — for the "no selection, multiple layers active" case
// ============================================================
function srBuildAggregateNarrative(active){
  if(!active.length) return { sentence:'', state:'good', hasIssue:false };

  var layerNames = active.map(function(L){ return L.name; });
  var totalAnomalies = 0;
  var totalAlerts = 0;
  var lowScores = [];
  var goodScores = [];

  active.forEach(function(L){
    var ins = LAYER_INSIGHTS[L.id];
    if(!ins) return;
    if(ins.anom) totalAnomalies += ins.anom.length;
    if(ins.alerts) totalAlerts += ins.alerts.length;
    // Try to find score in KPIs
    var hasScore = false;
    if(ins.kpis){
      ins.kpis.forEach(function(k){
        if(k[0] === 'Score' && typeof k[1] === 'string'){
          var m = k[1].match(/(\d+)/);
          if(m){
            var s = parseInt(m[1], 10);
            if(s < 85) lowScores.push(L.name);
            else goodScores.push(L.name);
            hasScore = true;
          }
        }
      });
    }
    if(!hasScore && (!ins.anom || !ins.anom.length)) goodScores.push(L.name);
  });

  var hasIssue = totalAnomalies > 0 || totalAlerts > 0;
  var state = totalAlerts > 0 ? 'crit' : (totalAnomalies > 0 ? 'warn' : 'good');

  var entity = active.length === 1 ? active[0].name : (active.length + ' active layers');
  var sentence;
  if(active.length === 1){
    var L = active[0];
    var ins = LAYER_INSIGHTS[L.id];
    if(ins){
      // Synthesize a det-shape from LAYER_INSIGHTS and reuse single-layer narrative
      var det = {
        tag: L.name,
        kpis: ins.kpis || [],
        anom: ins.anom || [],
        alerts: ins.alerts || [],
        recs: ins.recs || []
      };
      return srBuildNarrative(L.id, det);
    }
    sentence = L.name + ' is active.';
  } else if(!hasIssue){
    sentence = 'Across ' + entity + ' (' + srJoinList(layerNames) + '), all factors are within thresholds. Recommend no action — cleared for review.';
  } else {
    var issueCount = totalAnomalies + totalAlerts;
    sentence = 'Across ' + entity + ' (' + srJoinList(layerNames) + '), ' + issueCount + ' issue' + (issueCount === 1 ? '' : 's') + ' surfaced. Recommend opening individual layers below to investigate.';
  }

  return { sentence: sentence, state: state, hasIssue: hasIssue };
}

// ============================================================
// RENDER HELPERS for the new panel
// ============================================================
function srRenderNarrativeBlock(narr){
  var dotClass = narr.state === 'crit' ? 'crit' : narr.state === 'warn' ? 'warn' : 'good';
  var stateLabel = narr.state === 'crit' ? 'Needs attention' : narr.state === 'warn' ? 'Review' : 'Good';
  return '<div class="sr-narr">' +
    '<div class="sr-narr-meta"><span class="sr-narr-dot ' + dotClass + '"></span><span class="sr-narr-state">' + stateLabel + '</span></div>' +
    '<div class="sr-narr-text">' + narr.sentence + '</div>' +
  '</div>';
}

function srRenderDetailsBlock(innerHtml){
  return '<div class="sr-details" id="sr-details-block">' +
    '<button class="sr-details-toggle" onclick="srToggleDetails(this)">' +
      '<svg class="sr-details-chev" width="10" height="10" viewBox="0 0 10 10" fill="none">' +
        '<path d="M3 2L7 5L3 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</svg>' +
      '<span>Details</span>' +
    '</button>' +
    '<div class="sr-details-body" style="display:none;">' + innerHtml + '</div>' +
  '</div>';
}

function srToggleDetails(btn){
  var block = btn.parentNode;
  var body = block.querySelector('.sr-details-body');
  var chev = btn.querySelector('.sr-details-chev');
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  chev.style.transform = open ? '' : 'rotate(90deg)';
  block.classList.toggle('expanded', !open);
}


function isLayerActive(){
  var n=0; LAYERS.forEach(function(g){g.items.forEach(function(L){if(L.on)n++;});});
  return n>0;
}
function activeLayerCount(){var n=0,t=0; LAYERS.forEach(function(g){g.items.forEach(function(L){t++;if(L.on)n++;});}); return [n,t];}

// ============================================================
// LAYER PANEL — rewritten to use the stage-prototype patterns:
//   - lp-item row anatomy: eye / chevron / name / markers / state-dot
//   - eye toggle separate from row-click focus
//   - three styling states (default / focused / dimmed)
//   - HVY marker for heavy layers (mutual exclusion within group)
//   - parent/child sub-listing for multi-instance Analytics objects
//   - mixed-state eye when some children visible
//   - chevron expand/collapse with auto-expand on child focus
// ============================================================

// Eye icon SVGs
var SR_EYE_ON  = '<svg viewBox="0 0 14 14" fill="none"><path d="M1 7C1 7 3 3 7 3C11 3 13 7 13 7C13 7 11 11 7 11C3 11 1 7 1 7Z" stroke="currentColor" stroke-width="1.2"/><circle cx="7" cy="7" r="2" stroke="currentColor" stroke-width="1.2"/></svg>';
var SR_EYE_OFF = '<svg viewBox="0 0 14 14" fill="none"><path d="M2 2L12 12M3 7C3 7 4 9 7 9M11 7C11 7 10 5 7 5M5 5L4 4M9 9L10 10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
var SR_EYE_MIXED = '<svg viewBox="0 0 14 14" fill="none"><path d="M1 7C1 7 3 3 7 3C11 3 13 7 13 7C13 7 11 11 7 11C3 11 1 7 1 7Z" stroke="currentColor" stroke-width="1.2"/><path d="M4 7L10 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
var SR_EYE_SMALL_ON  = '<svg viewBox="0 0 12 12" fill="none"><path d="M1 6C1 6 2.5 2.5 6 2.5C9.5 2.5 11 6 11 6C11 6 9.5 9.5 6 9.5C2.5 9.5 1 6 1 6Z" stroke="currentColor" stroke-width="1"/><circle cx="6" cy="6" r="1.5" stroke="currentColor" stroke-width="1"/></svg>';
var SR_EYE_SMALL_OFF = '<svg viewBox="0 0 12 12" fill="none"><path d="M2 2L10 10M2.5 6C2.5 6 3.5 7.5 6 7.5M9.5 6C9.5 6 8.5 4.5 6 4.5" stroke="currentColor" stroke-width="1" stroke-linecap="round"/></svg>';
var SR_CHEVRON = '<svg viewBox="0 0 10 10" fill="none"><path d="M3 2L7 5L3 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// Per-multi-layer expansion state and child-visibility tracking
var SR_LAYER_UI = {
  expanded: {stockpiles:false, pits:false, dumps:false},
  // child visibility: childId -> true/false. defaults: parent.on means all children visible.
  childVisible: {stockpiles:{}, pits:{}, dumps:{}}
};

// Derive state ('good' | 'warn' | 'crit') for a child feature
function srChildState(f){
  if(!f) return 'good';
  if(typeof f.score==='number' && f.score<70) return 'crit';
  if(f.anomaly) return 'warn';
  if(typeof f.score==='number' && f.score<85) return 'warn';
  return 'good';
}

// Derive aggregate state for a parent multi-layer from its children
function srParentState(layerId){
  var feats = FEATURES[layerId];
  if(!Array.isArray(feats) || !feats.length) return 'good';
  var worst='good';
  for(var i=0;i<feats.length;i++){
    var s = srChildState(feats[i]);
    if(s==='crit') return 'crit';
    if(s==='warn') worst='warn';
  }
  return worst;
}

// Derive state for a singleton layer (heavy or simple) using LAYER_INSIGHTS
function srSingletonState(layerId){
  var ins = LAYER_INSIGHTS[layerId];
  if(!ins) return 'good';
  if(ins.anom && ins.anom.length) return 'warn';
  if(ins.alerts && ins.alerts.length) return 'warn';
  return 'good';
}

// Initialise child visibility for multi-layers (all children visible when parent is on)
function srInitChildVisibility(){
  ['stockpiles','pits','dumps'].forEach(function(id){
    var feats = FEATURES[id];
    if(!Array.isArray(feats)) return;
    var parent = srFindLayer(id);
    feats.forEach(function(f){
      if(SR_LAYER_UI.childVisible[id][f.id] === undefined){
        SR_LAYER_UI.childVisible[id][f.id] = parent ? !!parent.on : false;
      }
    });
  });
}

// Helper: find a layer by id
function srFindLayer(id){
  for(var g=0;g<LAYERS.length;g++){
    for(var i=0;i<LAYERS[g].items.length;i++){
      if(LAYERS[g].items[i].id===id) return LAYERS[g].items[i];
    }
  }
  return null;
}

// Helper: count visible children of a multi-layer
function srChildVisibleCount(parentId){
  var feats = FEATURES[parentId];
  if(!Array.isArray(feats)) return {visible:0,total:0};
  var map = SR_LAYER_UI.childVisible[parentId] || {};
  var v=0;
  feats.forEach(function(f){ if(map[f.id]) v++; });
  return {visible:v, total:feats.length};
}

// Mutual exclusion: if turning a heavy layer on, turn off other heavy layers in the same group
function srApplyHeavyExclusion(group, targetLayer){
  if(!targetLayer.heavy || !targetLayer.on) return;
  group.items.forEach(function(L){
    if(L!==targetLayer && L.heavy && L.on){
      L.on = false;
      if(SR.selected && SR.selected.layerId===L.id) SR.selected=null;
    }
  });
}

// Toggle a parent (singleton or multi). For multi, also syncs all children.
function srToggleParent(layer, group){
  layer.on = !layer.on;
  srApplyHeavyExclusion(group, layer);
  if(layer.multi){
    var map = SR_LAYER_UI.childVisible[layer.id];
    var feats = FEATURES[layer.id];
    if(Array.isArray(feats) && map){
      feats.forEach(function(f){ map[f.id] = layer.on; });
    }
  }
  // clear selection if its hosting layer is now off
  if(SR.selected && SR.selected.layerId===layer.id && !layer.on) SR.selected=null;
  buildLayerPanel();
  drawOverlay();
  buildInsights();
}

// Toggle a single child within a multi-layer
function srToggleChild(parentId, childId){
  var map = SR_LAYER_UI.childVisible[parentId];
  map[childId] = !map[childId];
  // Sync parent.on to reflect "any children visible"
  var parent = srFindLayer(parentId);
  var anyOn = false;
  var feats = FEATURES[parentId] || [];
  feats.forEach(function(f){ if(map[f.id]) anyOn = true; });
  parent.on = anyOn;
  if(SR.selected && SR.selected.layerId===parentId && !map[childId] && SR.selected.det && SR.selected.det.id===childId){
    SR.selected = null;
  }
  buildLayerPanel();
  drawOverlay();
  buildInsights();
}

// Focus a layer (row body click) — sets SR.selected to a layer-level focus.
// For multi-layers, focus the parent (aggregate). For singletons, focus the layer.
function srFocusLayer(layer){
  // Ensure visible if not already
  var grp = null;
  for(var g=0;g<LAYERS.length;g++){
    if(LAYERS[g].items.indexOf(layer)>=0){ grp = LAYERS[g]; break; }
  }
  if(!layer.on){
    layer.on = true;
    if(grp) srApplyHeavyExclusion(grp, layer);
    if(layer.multi){
      var map = SR_LAYER_UI.childVisible[layer.id];
      var feats = FEATURES[layer.id] || [];
      feats.forEach(function(f){ map[f.id] = true; });
    }
  }
  // Set SR.selected to a layer-level focus
  var ins = LAYER_INSIGHTS[layer.id] || {};
  SR.selected = {
    layerId: layer.id,
    det: {
      tag: layer.name,
      name: layer.name + ' \u00b7 layer overview',
      meta: (layer.heavy?'Heavy layer':layer.multi?'Multi-instance':'Single instance'),
      kpis: ins.kpis || [],
      recs: ins.recs || [],
      anom: ins.anom || [],
      alerts: ins.alerts || []
    }
  };
  buildLayerPanel();
  drawOverlay();
  buildInsights();
}

// Focus a specific child feature within a multi-layer
function srFocusChild(parentId, child){
  var parent = srFindLayer(parentId);
  // Make sure parent and child are visible
  var map = SR_LAYER_UI.childVisible[parentId];
  if(map && !map[child.id]){ map[child.id] = true; }
  if(parent && !parent.on){ parent.on = true; }
  // Auto-expand parent
  SR_LAYER_UI.expanded[parentId] = true;
  // Set SR.selected to this specific feature using the existing selection model shape
  SR.selected = {
    layerId: parentId,
    det: {
      tag: parent.name + ' / ' + (child.id || child.name),
      name: child.name || child.id,
      meta: child.vol ? (child.vol+' m^3') : (child.depth ? (child.depth+' m depth') : ''),
      kpis: [],
      recs: [],
      anom: child.anomaly ? [child.anomaly] : [],
      alerts: []
    }
  };
  buildLayerPanel();
  drawOverlay();
  buildInsights();
}

function buildLayerPanel(){
  var p=document.getElementById('sr-layers');
  // clear everything except header
  while(p.children.length>1) p.removeChild(p.lastChild);
  srInitChildVisibility();

  LAYERS.forEach(function(grp){
    var g=document.createElement('div'); g.className='sr-li2-grp';
    var gn=document.createElement('div'); gn.className='sr-li2-grp-name'; gn.textContent=grp.group;
    g.appendChild(gn);

    grp.items.forEach(function(L){
      var isMulti = !!L.multi;
      var isHeavy = !!L.heavy;
      var isFocused = !!(SR.selected && SR.selected.layerId===L.id && !(SR.selected.det && SR.selected.det.tag && SR.selected.det.tag.indexOf('/')>=0));

      // Determine eye state for multi-layers (on / off / mixed)
      var eyeState = 'off';
      if(isMulti){
        var c = srChildVisibleCount(L.id);
        if(c.visible===0) eyeState='off';
        else if(c.visible===c.total) eyeState='on';
        else eyeState='mixed';
      } else {
        eyeState = L.on ? 'on' : 'off';
      }

      // Build the row
      var row = document.createElement('div');
      row.className = 'sr-li2-row' + (isFocused?' focused':'');

      // For multi-layers, prepend chevron
      if(isMulti){
        var chev = document.createElement('div');
        chev.className = 'sr-li2-chevron' + (SR_LAYER_UI.expanded[L.id]?' expanded':'');
        chev.innerHTML = SR_CHEVRON;
        chev.title = SR_LAYER_UI.expanded[L.id]?'Collapse':'Expand';
        chev.onclick = (function(layerId){ return function(e){
          e.stopPropagation();
          SR_LAYER_UI.expanded[layerId] = !SR_LAYER_UI.expanded[layerId];
          buildLayerPanel();
        };})(L.id);
        row.appendChild(chev);
      }

      // Eye toggle
      var eye = document.createElement('div');
      eye.className = 'sr-li2-eye ' + eyeState;
      eye.innerHTML = (eyeState==='mixed') ? SR_EYE_MIXED : (eyeState==='on' ? SR_EYE_ON : SR_EYE_OFF);
      eye.title = (eyeState==='on'?'Hide':eyeState==='mixed'?'Show all':'Show');
      eye.onclick = (function(layer, group){ return function(e){
        e.stopPropagation();
        srToggleParent(layer, group);
      };})(L, grp);
      row.appendChild(eye);

      // Name
      var nm = document.createElement('span');
      nm.className = 'sr-li2-name';
      nm.textContent = L.name;
      row.appendChild(nm);

      // HVY marker (heavy layer)
      if(isHeavy){
        var hvy = document.createElement('span');
        hvy.className = 'sr-li2-hvy';
        hvy.textContent = 'HVY';
        hvy.title = 'Heavy layer \u00b7 mutual exclusion within group';
        row.appendChild(hvy);
      }

      // Count badge (multi-layer)
      if(isMulti){
        var feats = FEATURES[L.id];
        var n = Array.isArray(feats) ? feats.length : 0;
        var badge = document.createElement('span');
        badge.className = 'sr-li2-count';
        badge.textContent = String(n);
        row.appendChild(badge);
      }

      // State dot
      var stateClass;
      if(isMulti) stateClass = srParentState(L.id);
      else stateClass = srSingletonState(L.id);
      var dot = document.createElement('span');
      dot.className = 'sr-li2-state ' + stateClass;
      row.appendChild(dot);

      // Row body click → focus (excludes eye and chevron)
      row.onclick = (function(layer){ return function(){
        srFocusLayer(layer);
      };})(L);

      g.appendChild(row);

      // Nested children for multi-layers (when expanded)
      if(isMulti && SR_LAYER_UI.expanded[L.id]){
        var feats2 = FEATURES[L.id];
        if(Array.isArray(feats2) && feats2.length){
          var children = document.createElement('div');
          children.className = 'sr-li2-children';
          feats2.forEach(function(child){
            var visible = !!(SR_LAYER_UI.childVisible[L.id] && SR_LAYER_UI.childVisible[L.id][child.id]);
            var childFocused = !!(SR.selected && SR.selected.layerId===L.id && SR.selected.det && SR.selected.det.name===(child.name||child.id));
            var crow = document.createElement('div');
            crow.className = 'sr-li2-child' + (childFocused?' focused':'');

            var ceye = document.createElement('div');
            ceye.className = 'sr-li2-child-eye ' + (visible?'on':'off');
            ceye.innerHTML = visible ? SR_EYE_SMALL_ON : SR_EYE_SMALL_OFF;
            ceye.title = visible?'Hide':'Show';
            ceye.onclick = (function(pid, cid){ return function(e){
              e.stopPropagation();
              srToggleChild(pid, cid);
            };})(L.id, child.id);
            crow.appendChild(ceye);

            var cn = document.createElement('span');
            cn.className = 'sr-li2-child-name';
            cn.textContent = child.name || child.id;
            crow.appendChild(cn);

            var cstate = srChildState(child);
            var cdot = document.createElement('span');
            cdot.className = 'sr-li2-state ' + cstate;
            crow.appendChild(cdot);

            crow.onclick = (function(pid, c){ return function(){
              srFocusChild(pid, c);
            };})(L.id, child);

            children.appendChild(crow);
          });
          g.appendChild(children);
        }
      }
    });
    p.appendChild(g);
  });
}

function buildInsights(){
  var body=document.getElementById('sr-ibody');
  var title=document.getElementById('sr-ititle');
  var sub=document.getElementById('sr-isub');

  // SELECTED FEATURE MODE
  if(SR.selected){
    var det=SR.selected.det;
    title.textContent='Object Detail';
    sub.textContent=det.tag;
    var layerId = SR.selected.layerId;
    var narr = srBuildNarrative(layerId, det);

    // Score block: show the numeric score in the hero next to the name
    var scoreHtml = '';
    if(typeof narr.scoreVal === 'number'){
      scoreHtml = '<div class="sr-sel-score ' + narr.state + '">' + narr.scoreVal + '<span class="scoremax">/ 100</span></div>';
    }

    // Hero with Name + Score side-by-side
    var h='';
    h+='<div class="sr-sel-hero">'
      +'<div class="sr-sel-tag">'+det.tag+'</div>'
      +'<div class="sr-sel-namerow">'
        +'<div class="sr-sel-name">'+det.name+'</div>'
        + scoreHtml
      +'</div>'
      +'<div class="sr-sel-meta">'+det.meta+'</div>'
      +'<div class="sr-clear" onclick="srClearSel()">&#9664; Back to active layers</div>'
      +'</div>';

    // Narrative
    h += srRenderNarrativeBlock(narr);

    // Details (collapsed) — preserves the existing per-section structure inside
    var detailsBody = '';
    if(det.kpis && det.kpis.length) detailsBody += renderSection('Key Metrics', renderKPIs(det.kpis));
    if(det.anom && det.anom.length) detailsBody += renderSection('Anomalies', renderList(det.anom,'anom'));
    if(det.alerts && det.alerts.length) detailsBody += renderSection('Alerts', renderList(det.alerts,'alert'));
    if(det.recs && det.recs.length) detailsBody += renderSection('Recommendations', renderList(det.recs,'rec'));
    if(!detailsBody) detailsBody = '<div class="sr-empty" style="padding:14px 10px;">No additional detail available.</div>';
    h += srRenderDetailsBlock(detailsBody);

    body.innerHTML=h;
    return;
  }

  // ACTIVE LAYERS MODE (no selection)
  var active=[]; LAYERS.forEach(function(g){g.items.forEach(function(L){if(L.on)active.push(L);});});
  title.textContent='Insights';
  sub.textContent=active.length?(active.length+' Active Layer'+(active.length===1?'':'s')):'No Layers';
  if(!active.length){
    body.innerHTML='<div class="sr-empty">Toggle layers on the left to see insights.</div>';
    return;
  }

  // Single active layer: treat as if the user focused that layer.
  // Build a layer-level det directly from LAYER_INSIGHTS (avoids the per-feature
  // branches in featureDetail that would dereference undefined feat properties).
  // This eliminates the repetitive "Insights / Single layer active" hero.
  if(active.length === 1){
    var soleLayer = active[0];
    var soleDet = srLayerLevelDetail(soleLayer.id);
    if(soleDet){
      var soleNarr = srBuildNarrative(soleLayer.id, soleDet);
      var soleScoreHtml = '';
      if(typeof soleNarr.scoreVal === 'number'){
        soleScoreHtml = '<div class="sr-sel-score ' + soleNarr.state + '">' + soleNarr.scoreVal + '<span class="scoremax">/ 100</span></div>';
      }
      var hSole='';
      hSole += '<div class="sr-sel-hero">'
        + '<div class="sr-sel-tag">' + soleDet.tag + '</div>'
        + '<div class="sr-sel-namerow">'
          + '<div class="sr-sel-name">' + soleDet.name + '</div>'
          + soleScoreHtml
        + '</div>'
        + (soleDet.meta ? '<div class="sr-sel-meta">' + soleDet.meta + '</div>' : '')
        + '</div>';
      hSole += srRenderNarrativeBlock(soleNarr);

      var detailsBodySole = '';
      if(soleDet.kpis && soleDet.kpis.length) detailsBodySole += renderSection('Key Metrics', renderKPIs(soleDet.kpis));
      if(soleDet.anom && soleDet.anom.length) detailsBodySole += renderSection('Anomalies', renderList(soleDet.anom,'anom'));
      if(soleDet.alerts && soleDet.alerts.length) detailsBodySole += renderSection('Alerts', renderList(soleDet.alerts,'alert'));
      if(soleDet.recs && soleDet.recs.length) detailsBodySole += renderSection('Recommendations', renderList(soleDet.recs,'rec'));
      if(!detailsBodySole) detailsBodySole = '<div class="sr-empty" style="padding:14px 10px;">No additional detail available.</div>';
      hSole += srRenderDetailsBlock(detailsBodySole);

      body.innerHTML = hSole;
      return;
    }
  }

  // Multiple active layers: aggregate narrative across them
  var aggNarr = srBuildAggregateNarrative(active);

  // Aggregate hero — name + mean score across reporting layers
  var scores = [];
  active.forEach(function(L){
    var ins = LAYER_INSIGHTS[L.id];
    if(!ins) return;
    if(typeof ins.score === 'number') scores.push(ins.score);
  });
  // Per-feature scores from FEATURES (e.g. polygons)
  active.forEach(function(L){
    var feats = FEATURES[L.id];
    if(!Array.isArray(feats)) return;
    feats.forEach(function(f){
      if(f && typeof f.score === 'number') scores.push(f.score);
    });
  });
  var aggScore = null;
  if(scores.length){
    var sum = 0; scores.forEach(function(s){ sum += s; });
    aggScore = Math.round(sum / scores.length);
  }
  var aggState = aggScore !== null
    ? (aggScore < 70 ? 'crit' : aggScore < 85 ? 'warn' : 'good')
    : aggNarr.state;

  var heroName = active.length + ' active layers';
  var aggScoreHtml = '';
  if(aggScore !== null){
    aggScoreHtml = '<div class="sr-sel-score ' + aggState + '">' + aggScore + '<span class="scoremax">/ 100</span></div>';
  }

  var h2 = '';
  h2 += '<div class="sr-sel-hero">'
    + '<div class="sr-sel-namerow">'
      + '<div class="sr-sel-name">' + heroName + '</div>'
      + aggScoreHtml
    + '</div>'
    + '</div>';

  h2 += srRenderNarrativeBlock(aggNarr);

  // Aggregate Details — preserves the existing aggregate rendering
  var allKpis=[], allRecs=[], allAnom=[], allAlert=[];
  active.forEach(function(L){
    var ins=LAYER_INSIGHTS[L.id]; if(!ins) return;
    if(ins.kpis) ins.kpis.slice(0,2).forEach(function(k){allKpis.push([L.name+' / '+k[0], k[1]]);});
    if(ins.recs)  ins.recs.forEach(function(r){allRecs.push([L.name,r]);});
    if(ins.anom)  ins.anom.forEach(function(r){allAnom.push([L.name,r]);});
    if(ins.alerts)ins.alerts.forEach(function(r){allAlert.push([L.name,r]);});
  });
  // also surface per-feature anomalies on the map
  active.forEach(function(L){
    var feats=FEATURES[L.id];
    if(!Array.isArray(feats)) return;
    feats.forEach(function(f){
      if(f && f.anomaly){
        allAnom.push([L.name+' / '+(f.id||f.name||''), f.anomaly]);
      }
    });
  });

  var detailsBody2 = '';
  if(allAnom.length) detailsBody2 += renderSection('Anomalies', renderTaggedList(allAnom,'anom'));
  if(allAlert.length) detailsBody2 += renderSection('Alerts', renderTaggedList(allAlert,'alert'));
  if(allKpis.length) detailsBody2 += renderSection('Key Metrics', renderKPIs(allKpis.slice(0,8)));
  if(allRecs.length) detailsBody2 += renderSection('Recommendations', renderTaggedList(allRecs,'rec'));
  if(!detailsBody2) detailsBody2 = '<div class="sr-empty" style="padding:14px 10px;">No additional detail available.</div>';
  h2 += srRenderDetailsBlock(detailsBody2);

  body.innerHTML = h2;
}

function renderSection(name,inner){return '<div class="sr-isec"><div class="sr-isec-h">'+name+'</div>'+inner+'</div>';}
function renderKPIs(arr){
  return '<div class="sr-kpis">'+arr.map(function(k){
    return '<div class="sr-kpi"><div class="sr-kpi-k">'+k[0]+'</div><div class="sr-kpi-v">'+k[1]+'</div></div>';
  }).join('')+'</div>';
}
function renderList(items,cls){
  return '<div class="sr-list">'+items.map(function(t){
    return '<div class="sr-li '+cls+'">'+t+'</div>';
  }).join('')+'</div>';
}
function renderTaggedList(items,cls){
  return '<div class="sr-list">'+items.map(function(pair){
    return '<div class="sr-li '+cls+'"><span class="sr-li-tag">'+pair[0]+'</span>'+pair[1]+'</div>';
  }).join('')+'</div>';
}

function srClearSel(){SR.selected=null; drawOverlay(); buildInsights();}

// Panel collapse state
SR.layersCollapsed = false;
SR.insightsCollapsed = false;

function srToggleLayers(){
  SR.layersCollapsed = !SR.layersCollapsed;
  document.getElementById('sr-layers').classList.toggle('collapsed', SR.layersCollapsed);
  document.getElementById('sr-rail-l').classList.toggle('show', SR.layersCollapsed);
  document.getElementById('sr-modetog').classList.toggle('layers-collapsed', SR.layersCollapsed);
  document.getElementById('view-sr').classList.toggle('layers-collapsed', SR.layersCollapsed);
}

function srToggleInsights(){
  SR.insightsCollapsed = !SR.insightsCollapsed;
  document.getElementById('sr-insights').classList.toggle('collapsed', SR.insightsCollapsed);
  document.getElementById('sr-rail-r').classList.toggle('show', SR.insightsCollapsed);
  document.getElementById('view-sr').classList.toggle('insights-collapsed', SR.insightsCollapsed);
}

function srInitMap(){
  SR.canvas=document.getElementById('sr-map');
  SR.ctx=SR.canvas.getContext('2d');
  SR.ovl=document.getElementById('sr-overlay');
  // click on overlay
  SR.ovl.style.pointerEvents='auto';
  SR.ovl.addEventListener('click',function(e){
    var t=e.target;
    // Walk up to find ancestor with data-layer (handles clicks on inner SVG children)
    while(t && t !== SR.ovl && !(t.getAttribute && t.getAttribute('data-layer'))){
      t = t.parentNode;
    }
    if(t && t.getAttribute){
      var layerId=t.getAttribute('data-layer');
      var featId =t.getAttribute('data-feat');
      if(layerId && featId){
        // find feature
        var feat=null;
        if(layerId==='gcps') feat=FEATURES.gcps.find(function(g){return g.id===featId;});
        else if(layerId==='drone') feat=FEATURES.drone;
        else if(layerId==='base') feat=FEATURES.base;
        else if(layerId==='flight') feat={id:featId,name:featId};
        else if(['stockpiles','pits','dumps','cutfill'].indexOf(layerId)>=0)
          feat=FEATURES[layerId].find(function(f){return f.id===featId;});
        else if(['ortho','dsm','dtm','mesh','pcd','images'].indexOf(layerId)>=0)
          feat={id:featId, name:featId};  // synthetic feat — featureDetail will pull from LAYER_INSIGHTS
        if(feat){
          var det=featureDetail(layerId,feat);
          if(det){
            SR.selected={layerId:layerId, featId:featId, det:det};
            drawOverlay(); buildInsights();
          }
        }
      } else {
        // clicked empty area -> clear selection
        if(SR.selected){srClearSel();}
      }
    }
  });
  // mousemove -> update coord readout
  SR.ovl.addEventListener('mousemove',function(e){
    var r=SR.canvas.getBoundingClientRect();
    var nx=(e.clientX-r.left)/r.width, ny=(e.clientY-r.top)/r.height;
    var lat=(23.7250 - ny*.025).toFixed(4);
    var lng=(85.9220 + nx*.030).toFixed(4);
    document.getElementById('sr-coord').textContent=lat+'\u00B0 N   '+lng+'\u00B0 E  \u00b7  EPSG:32645';
  });
  window.addEventListener('resize',function(){if(currentModule==='sr')srResize();});
}

// ============================================================
// DRONE PAGE -- dedicated hardware view
// ============================================================

// Drone building blocks. Drawn from cbmi_master_ontology.yaml's Drone
// subsystem, plus two drone-asset-level blocks (Calibration State,
// Maintenance State) that don't appear at survey-job level.
var DRONE_BBS = [
  {id:'img', name:'Image Capture',
   score:88,
   desc:'Quality of the imagery delivered by the drone in this survey.',
   anomaly:false,
   indicators:[
     {name:'Image Validity',
      desc:'Share of images that are not corrupted, blurred beyond recognition, or otherwise unreadable.',
      sources:['Drone provenance log','Image quality log'],
      grades:[
       {l:'Excellent', r:'99% or more', s:100},
       {l:'Strong',    r:'97% or more', s:88, current:true},
       {l:'Acceptable',r:'94% or more', s:72},
       {l:'Marginal',  r:'90% or more', s:55},
       {l:'Critical',  r:'less than 90%',s:20}
      ],
      rec:'Validity sits in the Strong band. Continue current capture protocol.', alert:null},
     {name:'Image Geotagging',
      desc:'Share of images that arrived with embedded GPS coordinates in EXIF metadata.',
      sources:['Drone provenance log','Image EXIF metadata'],
      grades:[
       {l:'Excellent', r:'99% or more', s:100, current:true},
       {l:'Strong',    r:'97% or more', s:88},
       {l:'Acceptable',r:'93% or more', s:72},
       {l:'Marginal',  r:'88% or more', s:55},
       {l:'Critical',  r:'less than 85%',s:20}
      ],
      rec:'Every image carries a geotag. PPK refinement can proceed without gap-filling.', alert:null},
     {name:'Image Overlap',
      desc:'Lower of forward and side overlap measured across the survey.',
      sources:['Drone provenance log','Mission plan'],
      grades:[
       {l:'Excellent', r:'70% or more', s:100},
       {l:'Strong',    r:'60% or more', s:88, current:true},
       {l:'Acceptable',r:'50% or more', s:72},
       {l:'Marginal',  r:'40% or more', s:50},
       {l:'Critical',  r:'less than 40%',s:20}
      ],
      rec:'Overlap meets the Strong band. Consider raising to 80% on dense-feature areas.', alert:null},
     {name:'Image Format',
      desc:'Whether images were captured in a consistent format suitable for reconstruction.',
      sources:['Drone provenance log','Image EXIF metadata'],
      grades:[
       {l:'Raw',     r:'DNG or RAW',     s:100},
       {l:'JPG',     r:'JPG consistent', s:75, current:true},
       {l:'Mixed',   r:'Mixed DNG/JPG',  s:55}
      ],
      rec:'JPG-only is acceptable for survey work. Switch to RAW only if radiometric work is planned.', alert:null},
     {name:'Exposure Consistency',
      desc:'How stable exposure stayed across the mission.',
      sources:['Drone provenance log','Image EXIF metadata'],
      grades:[
       {l:'Tight',    r:'Coefficient under 0.05', s:100},
       {l:'Stable',   r:'Coefficient under 0.10', s:88, current:true},
       {l:'Variable', r:'Coefficient under 0.20', s:72},
       {l:'Loose',    r:'Coefficient under 0.35', s:50},
       {l:'Erratic',  r:'Coefficient 0.35 or more', s:25}
      ],
      rec:'Exposure held stable. No mosaic seam concerns.', alert:null}
   ]},
  {id:'mis', name:'Mission Execution',
   score:79,
   desc:'How well the drone executed the planned mission across area, ground sampling distance, overlap, altitude, conditions, and completion.',
   anomaly:true,
   indicators:[
     {name:'Mission Coverage',
      desc:'Share of the planned survey area actually covered by the flight.',
      sources:['Mission plan','Flight telemetry log'],
      grades:[
       {l:'Complete',  r:'99% or more',  s:100},
       {l:'Strong',    r:'97% or more',  s:88, current:true},
       {l:'Acceptable',r:'93% or more',  s:72},
       {l:'Partial',   r:'88% or more',  s:50},
       {l:'Gap',       r:'less than 88%',s:20, flag:'COVERAGE_GAP'}
      ],
      rec:'Coverage sits in the Strong band.', alert:null},
     {name:'GSD Execution',
      desc:'How close the executed ground sampling distance was to the planned value (ratio of executed to planned).',
      sources:['Mission plan','Image EXIF metadata'],
      grades:[
       {l:'On target',  r:'Ratio between 0.92 and 1.05', s:100},
       {l:'Acceptable', r:'Ratio between 0.85 and 1.10', s:85, current:true},
       {l:'Loose',      r:'Ratio between 0.78 and 1.18', s:68},
       {l:'Outside',    r:'Ratio outside the loose band',s:40}
      ],
      rec:'Executed GSD is within the acceptable band.', alert:null},
     {name:'Overlap Execution',
      desc:'How close the executed forward and side overlap was to the planned value, taken as the minimum of the two.',
      sources:['Mission plan','Image EXIF metadata'],
      grades:[
       {l:'On target',  r:'Minimum ratio between 0.95 and 1.10', s:100},
       {l:'Acceptable', r:'Minimum ratio between 0.88 and 1.15', s:85},
       {l:'Loose',      r:'Minimum ratio between 0.80 and 1.20', s:68, current:true},
       {l:'Outside',    r:'Minimum ratio under 0.80',            s:35}
      ],
      rec:'Overlap is at the loose end of the acceptable range. Tighten the overlap targets on the next mission.', alert:null},
     {name:'Altitude Execution',
      desc:'How close the executed flight altitude was to the planned value (ratio of executed to planned).',
      sources:['Mission plan','Flight telemetry log'],
      grades:[
       {l:'On target',  r:'Ratio between 0.95 and 1.05', s:100},
       {l:'Acceptable', r:'Ratio between 0.88 and 1.10', s:85, current:true},
       {l:'Loose',      r:'Ratio between 0.80 and 1.18', s:65},
       {l:'Outside',    r:'Ratio outside the loose band',s:35}
      ],
      rec:'Altitude was held within the acceptable band.', alert:null},
     {name:'Wind Conditions',
      desc:'Mean wind speed during the survey.',
      sources:['Flight telemetry log'],
      grades:[
       {l:'Calm',     r:'Mean under 5 m/s',   s:100},
       {l:'Light',    r:'Mean under 8 m/s',   s:85},
       {l:'Moderate', r:'Mean under 10 m/s',  s:65},
       {l:'Strong',   r:'Mean under 12 m/s',  s:45},
       {l:'High',     r:'Mean 12 m/s or more',s:20, current:true, flag:'HIGH_WIND_SURVEY'}
      ],
      rec:'Avoid flying in mean wind speeds above 12 m/s on future surveys.',
      alert:'Mean wind speed exceeded 12 m/s during this survey.'},
     {name:'Altitude Consistency',
      desc:'Standard deviation of altitude during the flight (distinct from offset against the planned altitude).',
      sources:['Flight telemetry log'],
      grades:[
       {l:'Tight',    r:'Standard deviation under 2 metres',   s:100},
       {l:'Stable',   r:'Standard deviation under 5 metres',   s:88, current:true},
       {l:'Variable', r:'Standard deviation under 10 metres',  s:65},
       {l:'Loose',    r:'Standard deviation under 20 metres',  s:40},
       {l:'Erratic',  r:'Standard deviation 20 metres or more',s:20, flag:'HIGH_ALTITUDE_VARIANCE'}
      ],
      rec:'Altitude held stable through the flight.', alert:null},
     {name:'Mission Completion',
      desc:'Ratio of mission steps completed against the plan.',
      sources:['Flight telemetry log','Mission plan'],
      grades:[
       {l:'Complete',     r:'Exactly 100%',  s:100},
       {l:'Near-complete',r:'97% or more',   s:85, current:true},
       {l:'Partial',      r:'93% or more',   s:65},
       {l:'Aborted',      r:'less than 93%', s:30}
      ],
      rec:'Mission executed near-completely.', alert:null}
   ]},
  {id:'gnss', name:'Rover GNSS Quality',
   score:91,
   desc:'Quality of the GNSS observations recorded by the rover (on the drone) across coverage, signal, frequency, continuity, acquisition, and sky view.',
   anomaly:false,
   indicators:[
     {name:'Rover Coverage',
      desc:'Whether the rover recorded continuously across the flight with adequate buffer time before and after the flight window.',
      sources:['Rover RINEX file','Flight telemetry log'],
      grades:[
       {l:'Generous',    r:'Ratio 1.10 or more with 120 second pre-flight and 60 second post-flight buffers', s:100},
       {l:'Sufficient',  r:'Ratio 1.00 or more with 60 second pre-flight buffer',                             s:88, current:true},
       {l:'Tight',       r:'Ratio 0.95 or more',                                                              s:72},
       {l:'Short',       r:'Ratio 0.85 or more',                                                              s:50},
       {l:'Insufficient',r:'Ratio under 0.85',                                                                s:20}
      ],
      rec:'Coverage meets the sufficient band with comfortable buffers.', alert:null},
     {name:'Signal Strength',
      desc:'Mean carrier-to-noise ratio and cycle slip count across the rover record during the flight.',
      sources:['Rover RINEX file'],
      grades:[
       {l:'Strong',  r:'35 dBHz or more with fewer than 5 cycle slips',  s:100},
       {l:'Good',    r:'32 dBHz or more with fewer than 10 cycle slips', s:85, current:true},
       {l:'Marginal',r:'28 dBHz or more or fewer than 20 cycle slips',   s:65},
       {l:'Weak',    r:'Below 28 dBHz or 20 or more cycle slips',        s:35}
      ],
      rec:'Signal sits in the Good band.', alert:null},
     {name:'Frequency Coverage',
      desc:'Whether the rover recorded dual-frequency observations.',
      sources:['Rover RINEX file'],
      grades:[
       {l:'Dual',  r:'Dual frequency available', s:100, current:true},
       {l:'Single',r:'L1 only',                  s:55}
      ],
      rec:'Dual frequency is available across the record.', alert:null},
     {name:'Continuity',
      desc:'Whether the rover record had any single gap exceeding 60 seconds during the flight window.',
      sources:['Rover RINEX file'],
      grades:[
       {l:'Continuous',r:'No gaps greater than 60 seconds', s:100, current:true},
       {l:'Broken',    r:'One or more gaps over 60 seconds',s:0, flag:'RINEX_CRITICAL_GAP'}
      ],
      rec:'Recording is continuous through the flight.', alert:null},
     {name:'Acquisition Time',
      desc:'Time from the first rover RINEX epoch to a healthy first epoch.',
      sources:['Rover RINEX file'],
      grades:[
       {l:'Fast',     r:'Under 60 seconds',  s:100, current:true},
       {l:'Good',     r:'Under 120 seconds', s:88},
       {l:'Slow',     r:'Under 300 seconds', s:65},
       {l:'Very slow',r:'300 seconds or more',s:30, flag:'SLOW_ROVER_ACQUISITION'}
      ],
      rec:'Rover acquired healthy lock quickly.', alert:null},
     {name:'Sky View',
      desc:'Mean and worst position dilution of precision across the flight window.',
      sources:['Rover RINEX file'],
      grades:[
       {l:'Open',      r:'Mean under 1.5 and maximum under 2.5', s:100},
       {l:'Good',      r:'Mean under 2.0 and maximum under 3.5', s:88, current:true},
       {l:'Restricted',r:'Mean under 3.0 and maximum under 5.0', s:65},
       {l:'Obstructed',r:'Mean under 6.0 or maximum under 8.0',  s:35},
       {l:'Poor',      r:'Maximum 8.0 or higher',                s:10, flag:'POOR_SKY_VIEW_DURING_FLIGHT'}
      ],
      rec:'Sky view is good throughout the flight.', alert:null}
   ]}
];

// Drone overall score = weighted mean (treat all BBs equal for simplicity)
var DRONE_OVERALL_SCORE = Math.round(DRONE_BBS.reduce(function(a,b){return a+b.score;},0)/DRONE_BBS.length);


// Historical trend data per BB. Last 10 surveys, oldest first.
// Survey id, date, score, anomaly flag, optional note.
var DRONE_TREND = {
  // overall drone score
  drone:[
    {sid:'S-038', date:'14 Oct 2025', score:84, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:87, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:81, anom:true,  note:'Adverse weather, GNSS gaps on landing'},
    {sid:'S-041', date:'05 Dec 2025', score:89, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:92, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:88, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:86, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:90, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:93, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:91, anom:false}
  ],
  img:[
    {sid:'S-038', date:'14 Oct 2025', score:85, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:88, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:79, anom:true,  note:'Exposure variance high'},
    {sid:'S-041', date:'05 Dec 2025', score:86, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:90, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:87, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:88, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:90, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:91, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:88, anom:false}
  ],
  mis:[
    {sid:'S-038', date:'14 Oct 2025', score:88, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:91, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:62, anom:true, note:'Mission aborted partway, weather'},
    {sid:'S-041', date:'05 Dec 2025', score:89, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:93, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:90, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:80, anom:true, note:'Short GNSS buffer'},
    {sid:'S-045', date:'14 Feb 2026', score:88, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:91, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:78, anom:true, note:'Short GNSS buffer'}
  ],
  gnss:[
    {sid:'S-038', date:'14 Oct 2025', score:87, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:89, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:75, anom:true, note:'GNSS gaps from cloud cover'},
    {sid:'S-041', date:'05 Dec 2025', score:90, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:93, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:91, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:90, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:92, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:94, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:92, anom:false}
  ]
};

// Fleet-median trend (mock, for "compare to fleet" overlay)
var DRONE_FLEET_MEDIAN = {
  drone:[82,84,80,86,88,86,85,87,89,88],
  img:[84,86,83,86,88,86,86,87,88,87],
  mis:[85,87,75,87,89,88,86,87,88,86],
  gnss:[85,87,80,88,90,89,88,89,90,90]
};

// Drone-page state
var DR_STATE = {
  selectedBB:null,           // null = overall, or BB id
  fleetCompare:false
};

// Helpers
function droneGradeFor(score){
  if(score>=90) return {name:'Gold',     cls:'dr-gr-gold'};
  if(score>=75) return {name:'Silver',   cls:'dr-gr-silver'};
  if(score>=60) return {name:'Bronze',   cls:'dr-gr-bronze'};
  if(score>=40) return {name:'Marginal', cls:'dr-gr-marginal'};
  return {name:'Poor', cls:'dr-gr-poor'};
}
function droneScoreColour(s){
  if(s>=90) return '#4db896';
  if(s>=75) return '#5596cc';
  if(s>=60) return '#c4882a';
  return '#b84444';
}
// Map score to state bucket for the design-system state colours.
// good (sage) for 85+, warn (gold) for 60-84, crit (red) for <60.
function droneStateFor(s){
  if(s>=85) return 'good';
  if(s>=60) return 'warn';
  return 'crit';
}

// ============================================================
// Drone page rendering
// ============================================================
function buildDronePage(){
  buildDroneRibbon();
  selectDroneBB(null); // overall by default
  drawDroneTrend();
}

function buildDroneRibbon(){
  var ribL=document.getElementById('dr-ribbon-l');
  var ribR=document.getElementById('dr-ribbon-r');
  if(!ribL || !ribR) return;

  // Split BBs into left and right rails. For drone (3 BBs): 2 on the left, 1 on the right.
  // This places the heaviest visual mass on the left where the eye starts reading.
  var splitIndex = Math.ceil(DRONE_BBS.length / 2);
  var leftBBs  = DRONE_BBS.slice(0, splitIndex);
  var rightBBs = DRONE_BBS.slice(splitIndex);

  function bbRowHtml(bb){
    var st=droneStateFor(bb.score);
    return '<div class="dr-bb" id="dr-bb-'+bb.id+'" onclick="onDroneBBClick(\''+bb.id+'\')">'
      +(bb.anomaly?'<div class="dr-bb-anom" title="Active alert"></div>':'')
      +'<button class="dr-bb-clear" onclick="event.stopPropagation(); clearDroneBBSelection();" title="Clear selection" aria-label="Clear selection">'
      +  '<svg viewBox="0 0 10 10" fill="none"><path d="M2 2L8 8M8 2L2 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>'
      +'</button>'
      +'<div class="dr-bb-head">'
      +  '<div class="dr-bb-name">'+bb.name+'</div>'
      +  '<div class="dr-bb-cluster">'
      +    '<span class="dr-bb-dot dot-'+st+'"></span>'
      +    '<span class="dr-bb-score state-'+st+'">'+bb.score+'<span style="font-size:.55em;font-weight:700;opacity:.45;vertical-align:super;line-height:0;">%</span></span>'
      +  '</div>'
      +'</div>'
      +'<div class="dr-bb-activelbl">Active filter</div>'
      +'</div>';
  }

  ribL.innerHTML = leftBBs.map(bbRowHtml).join('');
  ribR.innerHTML = rightBBs.map(bbRowHtml).join('');

  // Hover preview: hovering a BB card temporarily highlights the matching
  // part of the drone hero image. Leaving restores the persistent selection
  // (or clears the highlight if nothing is selected).
  DRONE_BBS.forEach(function(bb){
    var card = document.getElementById('dr-bb-'+bb.id);
    if(!card) return;
    card.addEventListener('mouseenter', function(){
      setDroneIllustHighlight(bb.id);
    });
    card.addEventListener('mouseleave', function(){
      setDroneIllustHighlight(DR_STATE.selectedBB);
    });
  });
}

// Click handler for BB cards. Toggles the card's selection: if it's already active,
// deselect and close the panel; otherwise select and open the panel.
function onDroneBBClick(bbId){
  if(DR_STATE.selectedBB === bbId){
    clearDroneBBSelection();
  } else {
    selectDroneBB(bbId);
    openDroneBBPanel(bbId);
  }
}

// Clear the active BB selection: returns the page to overall view and closes
// the panel if it's still open in BB mode.
function clearDroneBBSelection(){
  selectDroneBB(null);
  if(panelMode === 'bb'){
    closeDetail();
  }
}

function selectDroneBB(bbId){
  // bbId === null means overall drone view
  DR_STATE.selectedBB = bbId;
  // update ribbon active state
  document.querySelectorAll('.dr-bb').forEach(function(el){el.classList.remove('active');});
  if(bbId){
    var el=document.getElementById('dr-bb-'+bbId);
    if(el) el.classList.add('active');
  }
  // update hero illustration: highlight the corresponding physical part of the drone.
  // Hover handlers may also set the highlight temporarily; this re-applies the
  // persistent selection so leaving a hover returns us to the selected BB.
  setDroneIllustHighlight(bbId);
  // update hero
  updateDroneHero();
  // update trend
  drawDroneTrend();
  // update ops card
}

// Apply or clear the hero-image part highlight. Passing null clears all.
// Each BB id maps directly to a CSS modifier on the illustration container.
function setDroneIllustHighlight(bbId){
  var illust = document.getElementById('dr-illust');
  if(!illust) return;
  illust.classList.remove('highlight-img','highlight-mis','highlight-gnss');
  if(bbId) illust.classList.add('highlight-'+bbId);
}

function updateDroneHero(){
  var tag=document.getElementById('dr-hero-tag');
  var sc=document.getElementById('dr-hero-score');
  var gr=document.getElementById('dr-hero-grade');

  // Hero anchor: always the overall drone score and grade.
  // The big number is the page's identity and doesn't change when a BB is selected.
  // (Recommendations and alerts used to render below the hero; that content
  //  lives in the right panel now when the user opens a BB.)
  var overallGr = droneGradeFor(DRONE_OVERALL_SCORE);
  tag.textContent = 'Drone Score';
  sc.innerHTML = DRONE_OVERALL_SCORE + '<span class="dr-hero-pct">%</span>';
  gr.textContent = overallGr.name + ' Grade';
}

// Click a BB card -> open the existing detail panel with this BB's full story
function openDroneBBPanel(bbId){
  var bb=DRONE_BBS.find(function(b){return b.id===bbId;});
  if(!bb) return;
  // Reuse the existing renderIndicatorCard via buildPanel pattern, but with drone framing.
  panelMode='bb';
  buildDronePanel(bb);
  document.getElementById('detail-panel').classList.add('open');
  document.body.classList.add('panel-open');
}

function buildDronePanel(bb){
  var col=droneScoreColour(bb.score);

  document.getElementById('dp-chip').textContent='Drone Building Block';
  document.getElementById('dp-pname').textContent=bb.name;

  var inds=bb.indicators||[];

  // Classify indicators into triage buckets — same rule as the orbital BB panel.
  var classified = inds.map(function(ind){ return {ind:ind, info:classifyIndicator(ind)}; });
  var review  = classified.filter(function(c){ return c.info.needsReview; });
  var passing = classified.filter(function(c){ return !c.info.needsReview; });

  // Sort review by severity (crit before warn) then by lowest score
  review.sort(function(a,b){
    var stOrder = {crit:0, warn:1, good:2, neutral:3};
    var d = stOrder[a.info.state] - stOrder[b.info.state];
    if(d!==0) return d;
    var as = (a.info.score==null) ? 999 : a.info.score;
    var bs = (b.info.score==null) ? 999 : b.info.score;
    return as - bs;
  });

  // Sub-line: only when there's a mix of passing AND review
  var gsubEl = document.getElementById('dp-gsub');
  if(passing.length && review.length){
    gsubEl.textContent = inds.length + ' indicators \u00b7 '
                       + passing.length + ' passing \u00b7 '
                       + review.length + ' need'+(review.length===1?'s':'')+' review';
    gsubEl.style.display = '';
  } else {
    gsubEl.textContent = '';
    gsubEl.style.display = 'none';
  }

  var bs=document.getElementById('dp-bscore');
  bs.innerHTML=bb.score+'<span class="dp-bpct">%</span>';
  bs.style.color=col;

  // Hero status text dropped — score colour + section headers carry the state
  var statusEl = document.getElementById('dp-status');
  statusEl.textContent = '';
  statusEl.style.display = 'none';

  // Sibling tabs: all drone BBs
  var tabs='';
  DRONE_BBS.forEach(function(b2){
    var c2=droneScoreColour(b2.score);
    tabs+='<div class="dp-tab'+(b2.id===bb.id?' active':'')+'" onclick="switchDroneBBPanel(\''+b2.id+'\')">'
      +'<div class="dp-tab-dot" style="background:'+c2+'"></div>'+b2.name
      +' <span style="font-weight:700;color:'+c2+';margin-left:5px;">'+b2.score+'%</span></div>';
  });
  document.getElementById('dp-tabs').innerHTML=tabs;

  // ===== BODY ===== triage view: failing first (auto-expanded), then passing
  var body='';
  if(!inds.length){
    body+='<div class="ind-empty">No indicators defined.</div>';
  } else {
    if(review.length){
      body+='<div class="dp-sec dp-sec-review">Needs Review <span class="dp-sec-count">'+review.length+'</span></div>';
      review.forEach(function(c){
        body+=renderIndicatorCard(c.ind, /*autoExpand=*/true);
      });
    }
    if(passing.length){
      var passHeader = review.length ? 'Passing' : 'Indicators';
      body+='<div class="dp-sec">'+passHeader+' <span class="dp-sec-count">'+passing.length+'</span></div>';
      passing.forEach(function(c){
        body+=renderIndicatorCard(c.ind, /*autoExpand=*/false);
      });
    }
  }
  document.getElementById('dp-body').innerHTML=body;
}

function switchDroneBBPanel(bbId){
  var bb=DRONE_BBS.find(function(b){return b.id===bbId;});
  if(bb){
    selectDroneBB(bbId);
    buildDronePanel(bb);
  }
}

// ============================================================
// Trend graph
// ============================================================

// Trend modal — copy the currently-rendered trend SVG into a full-window overlay.
// Shared across all three hardware pages; the `which` argument selects the source.
function openTrendModal(which){
  var srcSvg, title;
  if(which === 'drone'){
    srcSvg = document.getElementById('dr-trend-svg');
    title = document.getElementById('dr-trend-tag') ? document.getElementById('dr-trend-tag').textContent : 'Drone Score Trend';
  } else if(which === 'base'){
    srcSvg = document.getElementById('bs-trend-svg');
    title = document.getElementById('bs-trend-tag') ? document.getElementById('bs-trend-tag').textContent : 'Base Station Score Trend';
  } else if(which === 'gcp'){
    srcSvg = document.getElementById('gc-trend-svg');
    title = document.getElementById('gc-trend-tag') ? document.getElementById('gc-trend-tag').textContent : 'Control Point Score Trend';
  } else {
    return;
  }
  if(!srcSvg) return;
  var dstSvg = document.getElementById('dr-trend-modal-svg');
  var titleEl = document.getElementById('dr-trend-modal-tag');
  if(dstSvg) dstSvg.innerHTML = srcSvg.innerHTML;
  if(titleEl) titleEl.textContent = title;
  document.getElementById('dr-trend-modal').classList.add('open');
}

function closeTrendModal(){
  var m = document.getElementById('dr-trend-modal');
  if(m) m.classList.remove('open');
}

// Clicking the modal backdrop (but not the card itself) closes it
function onTrendModalBackdropClick(evt){
  if(evt && evt.target && evt.target.id === 'dr-trend-modal') closeTrendModal();
}

// Esc key closes the trend modal when it's open
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape'){
    var m = document.getElementById('dr-trend-modal');
    if(m && m.classList.contains('open')) closeTrendModal();
  }
});

function toggleFleetCompare(){
  DR_STATE.fleetCompare = !DR_STATE.fleetCompare;
  document.getElementById('dr-trend-fleet').classList.toggle('on', DR_STATE.fleetCompare);
  drawDroneTrend();
}

function drawDroneTrend(){
  var svg=document.getElementById('dr-trend-svg');
  if(!svg) return;
  var key = DR_STATE.selectedBB || 'drone';
  var data = DRONE_TREND[key];
  var fleetData = DRONE_FLEET_MEDIAN[key];
  if(!data) return;

  // Update header
  var bb = DR_STATE.selectedBB ? DRONE_BBS.find(function(b){return b.id===DR_STATE.selectedBB;}) : null;
  document.getElementById('dr-trend-tag').textContent = bb ? (bb.name + ' Trend') : 'Drone Score Trend';

  // Layout: viewBox is 880 x 180
  var W=880, H=180;
  var padL=44, padR=20, padT=14, padB=30;
  var innerW=W-padL-padR, innerH=H-padT-padB;
  var n=data.length;
  var sx=function(i){return padL + (n>1?i/(n-1)*innerW:innerW/2);};
  var minScore=40, maxScore=100;
  var sy=function(s){return padT + (1 - (s-minScore)/(maxScore-minScore))*innerH;};

  // Build SVG content
  var s='';
  // axis lines (horizontal score gridlines)
  [40,60,80,100].forEach(function(y){
    s+='<line class="dr-tg-axis" x1="'+padL+'" y1="'+sy(y)+'" x2="'+(W-padR)+'" y2="'+sy(y)+'"/>';
    s+='<text class="dr-tg-tick" x="'+(padL-7)+'" y="'+(sy(y)+3)+'" text-anchor="end">'+y+'</text>';
  });
  // Highlight survey-grade band 85-100
  s+='<rect class="dr-tg-band" x="'+padL+'" y="'+sy(100)+'" width="'+innerW+'" height="'+(sy(85)-sy(100))+'"/>';

  // Area under line
  var pathArea='M '+padL+' '+sy(minScore);
  data.forEach(function(d,i){ pathArea += ' L '+sx(i)+' '+sy(d.score); });
  pathArea += ' L '+(W-padR)+' '+sy(minScore)+' Z';
  s+='<path class="dr-tg-area" d="'+pathArea+'"/>';

  // Fleet line (if active)
  if(DR_STATE.fleetCompare && fleetData){
    var fleetPath='';
    fleetData.forEach(function(v,i){
      fleetPath += (i===0?'M ':' L ')+sx(i)+' '+sy(v);
    });
    s+='<path class="dr-tg-fleet" d="'+fleetPath+'"/>';
    // legend dot at end
    var lastX=sx(fleetData.length-1);
    var lastY=sy(fleetData[fleetData.length-1]);
    s+='<text class="dr-tg-lbl" x="'+(lastX+6)+'" y="'+(lastY+3)+'" fill="rgba(255,255,255,.45)">fleet median</text>';
  }

  // Main line
  var path='';
  data.forEach(function(d,i){
    path += (i===0?'M ':' L ')+sx(i)+' '+sy(d.score);
  });
  s+='<path class="dr-tg-line" d="'+path+'"/>';

  // Points
  data.forEach(function(d,i){
    var x=sx(i), y=sy(d.score);
    var cls = d.anom ? 'dr-tg-pt anom' : 'dr-tg-pt';
    s+='<circle class="'+cls+'" cx="'+x+'" cy="'+y+'" r="4">'
      +'<title>'+d.sid+' \u00b7 '+d.date+' \u00b7 Score '+d.score+(d.note?'  ('+d.note+')':'')+'</title>'
      +'</circle>';
    // X-axis label every other point (to avoid clutter)
    if(i%2===0 || i===n-1){
      s+='<text class="dr-tg-tick" x="'+x+'" y="'+(H-padB+16)+'" text-anchor="middle">'+d.date.replace(/ 20\d\d/,'').trim()+'</text>';
    }
  });

  // Highlight the most recent survey with a "current" label
  var lastIdx=n-1;
  var lx=sx(lastIdx), ly=sy(data[lastIdx].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(0,180,216,.5)" stroke-width=".7"/>';
  s+='<text class="dr-tg-lbl" x="'+(lx-6)+'" y="'+(ly-12)+'" text-anchor="end" fill="var(--acc)">current</text>';

  svg.innerHTML = s;
}

// ============================================================
// BASE STATION PAGE -- dedicated hardware view
// ============================================================
// 4 BBs under PPK workflow, sourced from cbmi_master_ontology.yaml:
//   BB_base_rinex_recording_score   -- RINEX Recording Quality
//   BB_base_session_quality_score   -- Recording Session Quality
//   BB_base_antenna_setup_score     -- Antenna Setup Quality
//   BB_base_position_quality_score  -- Base Station Position Quality (PENDING Stage 2)
// BB_base_rtk_broadcast_score is disabled_in [PPK, NO_CORRECTION] -- hidden under PPK.

var BASE_BBS = [
  {id:'rinex', name:'RINEX Recording Quality',
   score:94,
   desc:'How well the base station recorded GNSS observations during the drone flight. Sets the ceiling for every position computed downstream.',
   anomaly:false,
   indicators:[
     {name:'Flight Coverage',
      desc:'Whether the base recorded continuously across the entire drone flight window, with adequate buffer time before and after takeoff.',
      sources:['Base Station RINEX file','Flight log'],
      grades:[
       {l:'Full',     r:'Full coverage with 120 second pre-flight and 60 second post-flight buffers', s:100, current:true},
       {l:'Acceptable',r:'Full coverage with at least 60 second pre-flight buffer', s:88},
       {l:'Tight',    r:'Full coverage but pre-flight buffer under 60 seconds', s:72},
       {l:'Gap',      r:'Base was not recording for part of the flight', s:0, flag:'BASE_RINEX_FLIGHT_GAP'}
      ],
      rec:'Coverage was full with comfortable buffers.', alert:null},
     {name:'Signal Strength',
      desc:'Average carrier-to-noise ratio at the base, with cycle slip count, across the flight window.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Strong',  r:'38 dBHz or more with fewer than 3 cycle slips', s:100},
       {l:'Good',    r:'35 dBHz or more with fewer than 8 cycle slips', s:88, current:true},
       {l:'Marginal',r:'30 dBHz or more with fewer than 15 cycle slips',s:65},
       {l:'Weak',    r:'Below 30 dBHz or 15 or more cycle slips',       s:35, flag:'BASE_SIGNAL_DEGRADED'}
      ],
      rec:'Signal sits in the Good band.', alert:null},
     {name:'Frequency Coverage',
      desc:'Share of epochs with dual-frequency observations available. Dual frequency unlocks longer baselines and faster integer resolution.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Dual',     r:'95% or more of epochs are dual-frequency',     s:100, current:true},
       {l:'Mixed',    r:'80% to 95% dual-frequency',                    s:70},
       {l:'Single',   r:'L1 only',                                      s:40, flag:'BASE_SINGLE_FREQUENCY'}
      ],
      rec:'Dual frequency is available throughout.', alert:null},
     {name:'Continuity',
      desc:'Whether any single gap in the RINEX record exceeded 60 seconds during the flight window.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Continuous',r:'No gaps greater than 60 seconds', s:100, current:true},
       {l:'Broken',    r:'One or more gaps over 60 seconds',s:0, flag:'BASE_RINEX_CRITICAL_GAP'}
      ],
      rec:'Recording is continuous through the flight.', alert:null}
   ]},
  {id:'session', name:'Recording Session Quality',
   score:88,
   desc:'Acquisition speed, sky view, multipath susceptibility, and session integrity over the base station\'s recording session.',
   anomaly:false,
   indicators:[
     {name:'Acquisition Time',
      desc:'Time from the first RINEX epoch to a healthy first epoch with at least four satellites and 30 dBHz signal.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Fast',     r:'Under 30 seconds',  s:100},
       {l:'Good',     r:'Under 60 seconds',  s:92, current:true},
       {l:'Slow',     r:'Under 120 seconds', s:75},
       {l:'Very slow',r:'Under 300 seconds', s:50},
       {l:'Critical', r:'300 seconds or more',s:20, flag:'SLOW_BASE_ACQUISITION'}
      ],
      rec:'Acquired healthy first epoch quickly.', alert:null},
     {name:'Sky View',
      desc:'Mean and worst position dilution of precision across the flight window. Lower values mean a more open sky and stronger geometry.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Open',     r:'Mean under 1.5 and maximum under 2.5',  s:100},
       {l:'Good',     r:'Mean under 2.0 and maximum under 3.5',  s:88, current:true},
       {l:'Restricted',r:'Mean under 3.0 and maximum under 5.0', s:65},
       {l:'Obstructed',r:'Mean under 6.0 or maximum under 8.0',  s:35},
       {l:'Poor',     r:'Maximum 8.0 or higher',                 s:10, flag:'BASE_POOR_SKY_VIEW'}
      ],
      rec:'Sky view is good throughout.', alert:null},
     {name:'Multipath Susceptibility',
      desc:'Variance in signal strength across satellites at the same elevation, which signals multipath contamination at the site.',
      sources:['Base Station RINEX file'],
      grades:[
       {l:'Low',      r:'Low variance',      s:100},
       {l:'Acceptable',r:'Moderate variance',s:80, current:true},
       {l:'Elevated', r:'Elevated variance', s:55},
       {l:'High',     r:'High variance',     s:25, flag:'BASE_HIGH_MULTIPATH_RISK'}
      ],
      rec:'Multipath risk is acceptable for this site.', alert:null},
     {name:'Session Integrity',
      desc:'Whether the session ended cleanly without unexpected shutdowns, with adequate battery margin throughout.',
      sources:['Operation log'],
      grades:[
       {l:'Clean',     r:'Completed normally, no shutdowns, minimum battery 30% or more',s:100, current:true},
       {l:'Acceptable',r:'Completed normally, no shutdowns, minimum battery 15% or more',s:85},
       {l:'Tight',     r:'Completed normally, no shutdowns, minimum battery under 15%',  s:60},
       {l:'Interrupted',r:'One or more unexpected shutdowns',                            s:20, flag:'BASE_SESSION_INTERRUPTED'}
      ],
      rec:'Session completed cleanly with comfortable battery margin.', alert:null}
   ]},
  {id:'antenna', name:'Antenna Setup Quality',
   score:72,
   desc:'Physical antenna setup over the base mark. Every millimetre of height error propagates one-for-one into the vertical accuracy of every downstream position.',
   anomaly:true,
   indicators:[
     {name:'Antenna Height Documented',
      desc:'Whether the antenna height above the base mark is documented, with the measurement method recorded.',
      sources:['Antenna setup record'],
      grades:[
       {l:'Documented',  r:'Documented with vertical method and three agreeing measurements', s:100},
       {l:'Documented',  r:'Documented with vertical method and one or two measurements',     s:88, current:true},
       {l:'Documented',  r:'Documented with slant method and correction applied',             s:72},
       {l:'Unspecified', r:'Documented but measurement method not specified',                 s:55, flag:'MEASUREMENT_METHOD_MISSING'},
       {l:'Missing',     r:'Antenna height not documented',                                   s:0,  flag:'ANTENNA_HEIGHT_MISSING'}
      ],
      rec:'Take three vertical measurements next setup to lift this to the highest band.', alert:null},
     {name:'Setup Verification',
      desc:'Whether the tripod was levelled and the setup was verified by a second person.',
      sources:['Antenna setup record'],
      grades:[
       {l:'Verified',     r:'Levelled and verified by a second person', s:100},
       {l:'Single person',r:'Levelled but single-person verification',  s:78, current:true},
       {l:'Unconfirmed',  r:'Level not confirmed',                      s:55, flag:'TRIPOD_LEVEL_UNCONFIRMED'},
       {l:'Unverified',   r:'No verification data',                     s:30, flag:'SETUP_UNVERIFIED'}
      ],
      rec:'Add second-person verification to the field protocol.',
      alert:'Setup was verified by a single person on this survey.'},
     {name:'Antenna Type Match',
      desc:'Whether the antenna type recorded in the setup matches the antenna type in the RINEX header.',
      sources:['Antenna setup record','Base Station RINEX file'],
      grades:[
       {l:'Match',   r:'Setup record and RINEX header agree',     s:100, current:true},
       {l:'Mismatch',r:'Setup record and RINEX header disagree',  s:20,  flag:'ANTENNA_TYPE_MISMATCH'},
       {l:'Unrecorded',r:'Antenna type not in setup record',      s:50}
      ],
      rec:'Antenna types match across both records.', alert:null}
   ]},
  // PENDING — resolves after Stage 2 Step A (Known Point Establishment).
  // We surface it so the user understands the score is not yet complete.
  {id:'position', name:'Base Station Position Quality',
   score:null,
   pending:true,
   desc:'Accuracy of the base station\'s own coordinates. Resolves after Stage 2 Step A (Known Point Establishment) -- either via CORS network processing or a customer-provided benchmark.',
   anomaly:false,
   indicators:[
     {name:'Known Point Accuracy',
      desc:'Horizontal and vertical accuracy of the established base position.',
      sources:['Stage 2 known-point report'],
      grades:[
       {l:'Survey',     r:'2 cm or better, CORS processed',  s:100},
       {l:'Provided',   r:'Customer-provided with stated accuracy', s:75},
       {l:'Unstated',   r:'No accuracy stated',              s:55, flag:'ACCURACY_NOT_STATED'}
      ],
      rec:null, alert:null},
     {name:'Position Source',
      desc:'How the base position was established.',
      sources:['Stage 2 known-point report'],
      grades:[
       {l:'CORS',       r:'CORS network adjustment',         s:100},
       {l:'Benchmark',  r:'Customer-provided benchmark',     s:75},
       {l:'Self',       r:'Self-occupied point',             s:55},
       {l:'Unknown',    r:'Source not stated',               s:20}
      ],
      rec:null, alert:null}
   ]}
];

// Overall base station score under PPK:
// 0.45 * RINEX + 0.25 * Session + 0.30 * Antenna (Phase 1; Position resolves in Stage 2)
function computeBaseOverall(){
  var rinex=BASE_BBS.find(function(b){return b.id==='rinex';}).score;
  var sess =BASE_BBS.find(function(b){return b.id==='session';}).score;
  var ant  =BASE_BBS.find(function(b){return b.id==='antenna';}).score;
  return Math.round(0.45*rinex + 0.25*sess + 0.30*ant);
}
var BASE_OVERALL_SCORE = computeBaseOverall();

// Historical trend data per BB. Sample data for illustration; oldest first.
// Position is null while still PENDING.
var BASE_TREND = {
  base:[
    {sid:'S-038', date:'14 Oct 2025', score:84, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:87, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:79, anom:true, note:'Antenna height not documented'},
    {sid:'S-041', date:'05 Dec 2025', score:88, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:91, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:88, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:85, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:89, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:92, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:BASE_OVERALL_SCORE, anom:false}
  ],
  rinex:[
    {sid:'S-038', date:'14 Oct 2025', score:90, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:92, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:88, anom:false},
    {sid:'S-041', date:'05 Dec 2025', score:93, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:95, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:93, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:91, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:94, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:96, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:94, anom:false}
  ],
  session:[
    {sid:'S-038', date:'14 Oct 2025', score:84, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:86, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:80, anom:false},
    {sid:'S-041', date:'05 Dec 2025', score:87, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:89, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:87, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:86, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:88, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:90, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:88, anom:false}
  ],
  antenna:[
    {sid:'S-038', date:'14 Oct 2025', score:75, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:78, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:55, anom:true, note:'Antenna height not documented'},
    {sid:'S-041', date:'05 Dec 2025', score:78, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:80, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:75, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:72, anom:true, note:'Single-person setup verification'},
    {sid:'S-045', date:'14 Feb 2026', score:78, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:80, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:72, anom:true, note:'Single-person setup verification'}
  ],
  position:[]  // pending across all surveys until Stage 2 completes
};

// Fleet median (sample comparison data)
var BASE_FLEET_MEDIAN = {
  base   :[82,84,80,86,88,86,85,87,89,88],
  rinex  :[89,90,88,91,92,91,90,92,93,92],
  session:[82,84,80,86,87,86,84,87,88,87],
  antenna:[76,78,72,78,80,77,75,78,79,76],
  position:[]
};

var BS_STATE = {
  selectedBB:null,
  fleetCompare:false
};

function baseGradeFor(score){
  if(score==null) return {name:'Pending', cls:'dr-gr-marginal'};
  if(score>=90) return {name:'Gold',     cls:'dr-gr-gold'};
  if(score>=75) return {name:'Silver',   cls:'dr-gr-silver'};
  if(score>=60) return {name:'Bronze',   cls:'dr-gr-bronze'};
  if(score>=40) return {name:'Marginal', cls:'dr-gr-marginal'};
  return {name:'Poor', cls:'dr-gr-poor'};
}
function baseScoreColour(s){
  if(s==null) return 'rgba(255,255,255,.5)';
  if(s>=90) return '#4db896';
  if(s>=75) return '#5596cc';
  if(s>=60) return '#c4882a';
  return '#b84444';
}

// ============================================================
// Base station rendering
// ============================================================
function buildBasePage(){
  buildBaseRibbon();
  selectBaseBB(null);
  drawBaseTrend();
}

function buildBaseRibbon(){
  var ribL=document.getElementById('bs-ribbon-l');
  var ribR=document.getElementById('bs-ribbon-r');
  if(!ribL || !ribR) return;

  // Split BBs into left and right rails. For Base (4 BBs): 2 on each side.
  var splitIndex = Math.ceil(BASE_BBS.length / 2);
  var leftBBs  = BASE_BBS.slice(0, splitIndex);
  var rightBBs = BASE_BBS.slice(splitIndex);

  function bbRowHtml(bb){
    var st=bb.pending ? 'neutral' : droneStateFor(bb.score);
    var dotCls = bb.pending ? '' : 'dot-'+st;
    var scoreCls = bb.pending ? '' : 'state-'+st;
    var displayScore = bb.pending ? '—' : bb.score;
    var displayPct = bb.pending
      ? '<span style="font-size:.55em;color:rgba(200,215,228,.42);font-weight:500;letter-spacing:.08em;text-transform:uppercase;margin-left:6px;">pending</span>'
      : '<span style="font-size:.55em;font-weight:700;opacity:.45;vertical-align:super;line-height:0;">%</span>';
    return '<div class="dr-bb'+(bb.pending?' is-pending':'')+'" id="bs-bb-'+bb.id+'" onclick="onBaseBBClick(\''+bb.id+'\')">'
      +(bb.anomaly?'<div class="dr-bb-anom" title="Active alert"></div>':'')
      +'<button class="dr-bb-clear" onclick="event.stopPropagation(); clearBaseBBSelection();" title="Clear selection" aria-label="Clear selection">'
      +  '<svg viewBox="0 0 10 10" fill="none"><path d="M2 2L8 8M8 2L2 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>'
      +'</button>'
      +'<div class="dr-bb-head">'
      +  '<div class="dr-bb-name">'+bb.name+'</div>'
      +  '<div class="dr-bb-cluster">'
      +    (bb.pending ? '' : '<span class="dr-bb-dot '+dotCls+'"></span>')
      +    '<span class="dr-bb-score '+scoreCls+'">'+displayScore+displayPct+'</span>'
      +  '</div>'
      +'</div>'
      +'<div class="dr-bb-activelbl">Active filter</div>'
      +'</div>';
  }

  ribL.innerHTML = leftBBs.map(bbRowHtml).join('');
  ribR.innerHTML = rightBBs.map(bbRowHtml).join('');

  // Hover preview: hovering a BB card temporarily highlights the matching
  // part of the base station hero image. Leaving restores the persistent selection.
  BASE_BBS.forEach(function(bb){
    var card = document.getElementById('bs-bb-'+bb.id);
    if(!card) return;
    card.addEventListener('mouseenter', function(){
      setBaseIllustHighlight(bb.id);
    });
    card.addEventListener('mouseleave', function(){
      setBaseIllustHighlight(BS_STATE.selectedBB);
    });
  });
}

function onBaseBBClick(bbId){
  if(BS_STATE.selectedBB === bbId){
    clearBaseBBSelection();
  } else {
    selectBaseBB(bbId);
    openBaseBBPanel(bbId);
  }
}

function clearBaseBBSelection(){
  selectBaseBB(null);
  if(panelMode === 'bb'){
    closeDetail();
  }
}

function selectBaseBB(bbId){
  BS_STATE.selectedBB = bbId;
  document.querySelectorAll('#bs-ribbon .dr-bb').forEach(function(el){el.classList.remove('active');});
  if(bbId){
    var el=document.getElementById('bs-bb-'+bbId);
    if(el) el.classList.add('active');
  }
  // Light up the matching physical part of the base station hero image.
  setBaseIllustHighlight(bbId);
  updateBaseHero();
  drawBaseTrend();
}

// Apply or clear the base-station hero part highlight. null clears all.
function setBaseIllustHighlight(bbId){
  var illust = document.getElementById('bs-illust');
  if(!illust) return;
  illust.classList.remove('highlight-rinex','highlight-session','highlight-antenna','highlight-position');
  if(bbId) illust.classList.add('highlight-'+bbId);
}

function updateBaseHero(){
  var tag=document.getElementById('bs-hero-tag');
  var sc=document.getElementById('bs-hero-score');
  var gr=document.getElementById('bs-hero-grade');

  // Hero: always overall base station score (Phase 1 under PPK).
  // Recommendations and alerts have moved to the right panel.
  var overallGr = baseGradeFor(BASE_OVERALL_SCORE);
  tag.textContent = 'Base Station Score';
  sc.innerHTML = BASE_OVERALL_SCORE + '<span class="dr-hero-pct">%</span>';
  gr.textContent = overallGr.name + ' Grade';
}

function openBaseBBPanel(bbId){
  var bb=BASE_BBS.find(function(b){return b.id===bbId;});
  if(!bb) return;
  panelMode='bb';
  buildBasePanel(bb);
  document.getElementById('detail-panel').classList.add('open');
  document.body.classList.add('panel-open');
}

function buildBasePanel(bb){
  var col = bb.pending ? 'rgba(255,255,255,.5)' : baseScoreColour(bb.score);

  document.getElementById('dp-chip').textContent='Base Station Building Block';
  document.getElementById('dp-pname').textContent=bb.name;

  var inds=bb.indicators||[];

  // Triage classification — skipped for pending BBs (their indicators describe
  // what WILL be measured, not what failed). Pending BBs render flat + descriptive.
  var classified = bb.pending ? [] : inds.map(function(ind){ return {ind:ind, info:classifyIndicator(ind)}; });
  var review  = classified.filter(function(c){ return c.info.needsReview; });
  var passing = classified.filter(function(c){ return !c.info.needsReview; });
  review.sort(function(a,b){
    var stOrder = {crit:0, warn:1, good:2, neutral:3};
    var d = stOrder[a.info.state] - stOrder[b.info.state];
    if(d!==0) return d;
    var as = (a.info.score==null) ? 999 : a.info.score;
    var bs = (b.info.score==null) ? 999 : b.info.score;
    return as - bs;
  });

  // Sub-line: pending uses dedicated message; otherwise mixed-only triage tally
  var gsubEl = document.getElementById('dp-gsub');
  if(bb.pending){
    gsubEl.textContent = 'Pending Stage 2 \u00b7 ' + inds.length + ' indicator'+(inds.length===1?'':'s')+' will resolve';
    gsubEl.style.display = '';
  } else if(passing.length && review.length){
    gsubEl.textContent = inds.length + ' indicators \u00b7 '
                       + passing.length + ' passing \u00b7 '
                       + review.length + ' need'+(review.length===1?'s':'')+' review';
    gsubEl.style.display = '';
  } else {
    gsubEl.textContent = '';
    gsubEl.style.display = 'none';
  }

  var bs=document.getElementById('dp-bscore');
  if(bb.pending){
    bs.innerHTML='&mdash;<span class="dp-bpct" style="font-size:.4em;letter-spacing:.06em;">PENDING</span>';
  } else {
    bs.innerHTML=bb.score+'<span class="dp-bpct">%</span>';
  }
  bs.style.color=col;

  // Hero status text dropped — score colour + sub-line carry the state
  var statusEl = document.getElementById('dp-status');
  statusEl.textContent = '';
  statusEl.style.display = 'none';

  // Sibling tabs: all base BBs
  var tabs='';
  BASE_BBS.forEach(function(b2){
    var c2 = b2.pending ? 'rgba(255,255,255,.4)' : baseScoreColour(b2.score);
    var sc2 = b2.pending ? '&mdash;' : b2.score+'%';
    tabs+='<div class="dp-tab'+(b2.id===bb.id?' active':'')+'" onclick="switchBaseBBPanel(\''+b2.id+'\')">'
      +'<div class="dp-tab-dot" style="background:'+c2+'"></div>'+b2.name
      +' <span style="font-weight:700;color:'+c2+';margin-left:5px;">'+sc2+'</span></div>';
  });
  document.getElementById('dp-tabs').innerHTML=tabs;

  // ===== BODY =====
  var body='';
  if(bb.pending){
    // Pending BB: explain the deferral, then list indicators flat + collapsed
    body+='<div class="dp-sec">Pending</div>'
      +'<div class="bb-rule">'
      +'<div class="bb-rule-lbl">Stage 2 deferred</div>'
      +'<div class="bb-rule-body">This building block resolves once Stage 2 Step A (Known Point Establishment) completes. The indicators below describe what will be measured.</div>'
      +'</div>';
    body+='<div class="dp-sec">Indicators <span class="dp-sec-count">'+inds.length+'</span></div>';
    if(!inds.length){
      body+='<div class="ind-empty">No indicators defined.</div>';
    } else {
      inds.forEach(function(ind){
        body+=renderIndicatorCard(ind, /*autoExpand=*/false);
      });
    }
  } else {
    // Active BB: triage view (failing first auto-expanded, then passing)
    if(!inds.length){
      body+='<div class="ind-empty">No indicators defined.</div>';
    } else {
      if(review.length){
        body+='<div class="dp-sec dp-sec-review">Needs Review <span class="dp-sec-count">'+review.length+'</span></div>';
        review.forEach(function(c){
          body+=renderIndicatorCard(c.ind, /*autoExpand=*/true);
        });
      }
      if(passing.length){
        var passHeader = review.length ? 'Passing' : 'Indicators';
        body+='<div class="dp-sec">'+passHeader+' <span class="dp-sec-count">'+passing.length+'</span></div>';
        passing.forEach(function(c){
          body+=renderIndicatorCard(c.ind, /*autoExpand=*/false);
        });
      }
    }
  }
  document.getElementById('dp-body').innerHTML=body;
}

function switchBaseBBPanel(bbId){
  var bb=BASE_BBS.find(function(b){return b.id===bbId;});
  if(bb){ selectBaseBB(bbId); buildBasePanel(bb); }
}

function toggleBaseFleetCompare(){
  BS_STATE.fleetCompare = !BS_STATE.fleetCompare;
  document.getElementById('bs-trend-fleet').classList.toggle('on', BS_STATE.fleetCompare);
  drawBaseTrend();
}

function drawBaseTrend(){
  var svg=document.getElementById('bs-trend-svg');
  if(!svg) return;
  var key = BS_STATE.selectedBB || 'base';
  var data = BASE_TREND[key] || [];
  var fleetData = BASE_FLEET_MEDIAN[key] || [];

  var bb = BS_STATE.selectedBB ? BASE_BBS.find(function(b){return b.id===BS_STATE.selectedBB;}) : null;
  document.getElementById('bs-trend-tag').textContent = bb ? (bb.name + ' Trend') : 'Base Station Score Trend';

  // If pending or no data, render an empty-state frame
  if(!data.length){
    svg.innerHTML = '<text x="440" y="90" text-anchor="middle" fill="rgba(255,255,255,.45)" font-family="IBM Plex Mono" font-size="11" letter-spacing=".06em">Trend resolves after Stage 2 Step A completes</text>';
    return;
  }

  var W=880, H=180, padL=44, padR=20, padT=14, padB=30;
  var innerW=W-padL-padR, innerH=H-padT-padB;
  var n=data.length;
  var sx=function(i){return padL + (n>1?i/(n-1)*innerW:innerW/2);};
  var minScore=40, maxScore=100;
  var sy=function(s){return padT + (1 - (s-minScore)/(maxScore-minScore))*innerH;};

  var s='';
  [40,60,80,100].forEach(function(y){
    s+='<line class="dr-tg-axis" x1="'+padL+'" y1="'+sy(y)+'" x2="'+(W-padR)+'" y2="'+sy(y)+'"/>';
    s+='<text class="dr-tg-tick" x="'+(padL-7)+'" y="'+(sy(y)+3)+'" text-anchor="end">'+y+'</text>';
  });
  s+='<rect class="dr-tg-band" x="'+padL+'" y="'+sy(100)+'" width="'+innerW+'" height="'+(sy(85)-sy(100))+'"/>';

  var pathArea='M '+padL+' '+sy(minScore);
  data.forEach(function(d,i){ pathArea += ' L '+sx(i)+' '+sy(d.score); });
  pathArea += ' L '+(W-padR)+' '+sy(minScore)+' Z';
  s+='<path class="dr-tg-area" d="'+pathArea+'"/>';

  if(BS_STATE.fleetCompare && fleetData.length){
    var fleetPath='';
    fleetData.forEach(function(v,i){ fleetPath += (i===0?'M ':' L ')+sx(i)+' '+sy(v); });
    s+='<path class="dr-tg-fleet" d="'+fleetPath+'"/>';
    var lastX=sx(fleetData.length-1);
    var lastY=sy(fleetData[fleetData.length-1]);
    s+='<text class="dr-tg-lbl" x="'+(lastX+6)+'" y="'+(lastY+3)+'" fill="rgba(255,255,255,.45)">fleet median</text>';
  }

  var path='';
  data.forEach(function(d,i){ path += (i===0?'M ':' L ')+sx(i)+' '+sy(d.score); });
  s+='<path class="dr-tg-line" d="'+path+'"/>';

  data.forEach(function(d,i){
    var x=sx(i), y=sy(d.score);
    var cls = d.anom ? 'dr-tg-pt anom' : 'dr-tg-pt';
    s+='<circle class="'+cls+'" cx="'+x+'" cy="'+y+'" r="4">'
      +'<title>'+d.sid+' \u00b7 '+d.date+' \u00b7 Score '+d.score+(d.note?'  ('+d.note+')':'')+'</title>'
      +'</circle>';
    if(i%2===0 || i===n-1){
      s+='<text class="dr-tg-tick" x="'+x+'" y="'+(H-padB+16)+'" text-anchor="middle">'+d.date.replace(/ 20\d\d/,'').trim()+'</text>';
    }
  });

  var lastIdx=n-1;
  var lx=sx(lastIdx), ly=sy(data[lastIdx].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(0,180,216,.5)" stroke-width=".7"/>';
  s+='<text class="dr-tg-lbl" x="'+(lx-6)+'" y="'+(ly-12)+'" text-anchor="end" fill="var(--acc)">current</text>';

  svg.innerHTML=s;
}

// ============================================================
// Control Point PAGE -- dedicated hardware view
// ============================================================
// 4 BBs under Path 1 (RINEX-Recorded, standard + X/AeroPoints):
//   BB_gcp_device_recording_score  -- Control Point Device Recording Quality (mean across N devices)
//   BB_gcp_session_quality_score   -- Control Point Device Session Quality (mean across N devices)
//   BB_gcp_layout_score            -- Control Point Network Layout Quality (system-level)
//   BB_gcp_coordinate_score        -- Control Point Coordinate Accuracy Quality (PENDING Stage 2 Step C)
// Path 4 (no Control Points) and Path 2 (pre-surveyed) disable some of these; PPK + Path 1 keeps all four
// (with the last one pending).

var GCP_BBS = [
  {id:'devrec', name:'Device Recording Quality',
   score:91,
   desc:'Quality of GNSS observations recorded by the network of Control Point devices, averaged across all devices in the field.',
   anomaly:false,
   aggregation:'mean across 6 devices',
   indicators:[
     {name:'Flight Coverage',
      desc:'Whether each device recorded continuously across the entire drone flight, with adequate buffer time before takeoff.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Full',     r:'Full coverage with 60 second pre-flight buffer or longer', s:100, current:true},
       {l:'Acceptable',r:'Full coverage with no pre-flight buffer',                  s:85},
       {l:'Gap',      r:'A device was not recording for part of the flight',         s:0, flag:'GCP_DEVICE_FLIGHT_GAP'}
      ],
      rec:'Coverage was complete across all devices.', alert:null},
     {name:'Signal Strength',
      desc:'Mean carrier-to-noise ratio and cycle slip count per device during the flight window.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Strong',  r:'38 dBHz or more with fewer than 3 cycle slips', s:100},
       {l:'Good',    r:'35 dBHz or more with fewer than 8 cycle slips', s:88, current:true},
       {l:'Marginal',r:'30 dBHz or more with fewer than 15 cycle slips',s:65},
       {l:'Weak',    r:'Below 30 dBHz or 15 or more cycle slips',       s:35, flag:'GCP_DEVICE_SIGNAL_DEGRADED'}
      ],
      rec:'Signal is in the Good band across devices.', alert:null},
     {name:'Frequency Coverage',
      desc:'Share of epochs with dual-frequency observations available, averaged across devices.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Dual',     r:'95% or more of epochs are dual-frequency', s:100, current:true},
       {l:'Mixed',    r:'80% to 95% dual-frequency',                s:70},
       {l:'Single',   r:'L1 only',                                  s:40, flag:'GCP_DEVICE_SINGLE_FREQUENCY'}
      ],
      rec:'Dual frequency is available across the network.', alert:null},
     {name:'Continuity',
      desc:'Whether any device experienced a single gap in its RINEX record exceeding 60 seconds during the flight window.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Continuous',r:'No gaps greater than 60 seconds on any device', s:100, current:true},
       {l:'Broken',    r:'One or more devices had a gap over 60 seconds', s:0, flag:'GCP_DEVICE_CRITICAL_GAP'}
      ],
      rec:'All devices recorded continuously.', alert:null}
   ]},
  {id:'session', name:'Device Session Quality',
   score:84,
   desc:'Per-device acquisition speed, sky view, multipath behaviour, and antenna setup integrity, averaged across the network.',
   anomaly:false,
   aggregation:'mean across 6 devices',
   indicators:[
     {name:'Acquisition Time',
      desc:'Time from first epoch to a healthy first epoch with at least four satellites and 30 dBHz signal, per device.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Fast',     r:'Under 30 seconds',  s:100},
       {l:'Good',     r:'Under 60 seconds',  s:92, current:true},
       {l:'Slow',     r:'Under 120 seconds', s:78},
       {l:'Very slow',r:'Under 300 seconds', s:50},
       {l:'Critical', r:'300 seconds or more',s:20, flag:'GCP_DEVICE_SLOW_ACQUISITION'}
      ],
      rec:'Devices acquired healthy first epoch quickly.', alert:null},
     {name:'Sky View',
      desc:'Mean and worst position dilution of precision during the flight window, per device.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Open',     r:'Mean under 1.5 and maximum under 2.5',  s:100},
       {l:'Good',     r:'Mean under 2.0 and maximum under 3.5',  s:88, current:true},
       {l:'Restricted',r:'Mean under 3.0 and maximum under 5.0', s:65},
       {l:'Obstructed',r:'Mean under 6.0 or maximum under 8.0',  s:35},
       {l:'Poor',     r:'Maximum 8.0 or higher',                 s:10, flag:'GCP_DEVICE_POOR_SKY_VIEW'}
      ],
      rec:'Sky view is good across the network.', alert:null},
     {name:'Multipath Susceptibility',
      desc:'Variance in signal strength across satellites at the same elevation, per device.',
      sources:['Control Point device RINEX files'],
      grades:[
       {l:'Low',      r:'Low variance',      s:100},
       {l:'Acceptable',r:'Moderate variance',s:80, current:true},
       {l:'Elevated', r:'Elevated variance', s:55},
       {l:'High',     r:'High variance',     s:25, flag:'GCP_DEVICE_HIGH_MULTIPATH'}
      ],
      rec:'Multipath risk is acceptable across devices.', alert:null},
     {name:'Antenna Height Documented',
      desc:'Whether each device\'s antenna height is documented. Auto-populated for X / AeroPoints devices; user-measured for pole-mounted devices.',
      sources:['Control Point layout record'],
      grades:[
       {l:'Auto',          r:'Auto-populated for X or AeroPoints devices',  s:100, current:true},
       {l:'Documented',    r:'User-entered with measurement method stated', s:90},
       {l:'Unspecified',   r:'User-entered, method not stated',             s:65},
       {l:'Missing',       r:'Not documented and not auto-populated',       s:0, flag:'GCP_DEVICE_ANTENNA_HEIGHT_MISSING'}
      ],
      rec:'Antenna height is auto-resolved across the network.', alert:null}
   ]},
  {id:'layout', name:'Network Layout Quality',
   score:82,
   desc:'System-level design of the Control Point network across the site: counts, check point coverage, spatial distribution, and target placement confirmation.',
   anomaly:true,
   indicators:[
     {name:'Control Point Count',
      desc:'Effective number of usable Control Points across the site (deployed count minus any failed devices in the Control Point role).',
      sources:['Control Point layout record','Control Point device RINEX files'],
      grades:[
       {l:'Excellent',   r:'10 or more usable Control Points',  s:100},
       {l:'Strong',      r:'7 or more',  s:90},
       {l:'Survey-grade',r:'5 or more',  s:82, current:true},
       {l:'Minimum',     r:'4',           s:68},
       {l:'Limited',     r:'3',           s:50},
       {l:'Critical',    r:'2',           s:15, flag:'INSUFFICIENT_GCP_COUNT'},
       {l:'Unusable',    r:'0 or 1',      s:0, flag:'NO_USABLE_GCPS'}
      ],
      rec:'Control Point count meets the survey-grade band. Consider adding a seventh device to lift this to Strong on dense-feature sites.', alert:null},
     {name:'Check Point Count',
      desc:'Effective number of usable check points (devices held back from ODM to verify accuracy independently).',
      sources:['Control Point layout record','Control Point device RINEX files'],
      grades:[
       {l:'Excellent',   r:'10 or more',  s:100},
       {l:'Strong',      r:'5 or more',   s:88, current:true},
       {l:'Adequate',    r:'3 or more',   s:65},
       {l:'Marginal',    r:'2',           s:35, flag:'INSUFFICIENT_CHECK_POINTS'},
       {l:'None',        r:'0 or 1',      s:0, flag:'NO_CHECK_POINTS'}
      ],
      rec:'Check point count supports statistically meaningful accuracy verification.', alert:null},
     {name:'Distribution',
      desc:'Whether the Control Point network covers the site boundary, with adequate spacing between devices.',
      sources:['Control Point layout record'],
      grades:[
       {l:'Even',         r:'Boundary covered with 50 metre spacing or more', s:100},
       {l:'Acceptable',   r:'Boundary covered with 20 metre spacing or more', s:85, current:true},
       {l:'Crowded',      r:'Boundary covered with less than 20 metre spacing', s:65},
       {l:'Edge missing', r:'Boundary not covered, spacing 50 metres or more', s:55},
       {l:'Poor',         r:'Boundary not covered with less than 20 metre spacing', s:25, flag:'POOR_GCP_DISTRIBUTION'}
      ],
      rec:'Network covers the site boundary with acceptable spacing.', alert:null},
     {name:'Target Placement',
      desc:'Whether each deployed device is confirmed in the layout record as physically placed on its intended target.',
      sources:['Control Point layout record'],
      grades:[
       {l:'All',     r:'All devices confirmed', s:100},
       {l:'Most',    r:'90% or more confirmed', s:80, current:true},
       {l:'Partial', r:'Under 90% confirmed',   s:50, flag:'TARGET_PLACEMENT_UNCONFIRMED'}
      ],
      rec:'Confirm placement on the remaining device for the next survey.',
      alert:'One of six devices has unconfirmed target placement.'}
   ]},
  // PENDING -- resolves after Stage 2 Step C (Control Point Coordinate Processing)
  {id:'coord', name:'Coordinate Accuracy Quality',
   score:null,
   pending:true,
   desc:'Accuracy of the computed Control Point coordinates. Resolves after Stage 2 Step C (Control Point Coordinate Processing). Combines horizontal accuracy, vertical accuracy, and the rigour of the processing method.',
   anomaly:false,
   indicators:[
     {name:'Horizontal Accuracy',
      desc:'Horizontal accuracy of the processed Control Point coordinates, in metres.',
      sources:['Stage 2 Control Point coordinate report'],
      grades:[
       {l:'Survey-grade',r:'Under 2 cm',           s:100},
       {l:'Engineering', r:'Under 5 cm',           s:88},
       {l:'Mapping',     r:'Under 10 cm',          s:65},
       {l:'Coarse',      r:'10 cm or worse',       s:35}
      ],
      rec:null, alert:null},
     {name:'Vertical Accuracy',
      desc:'Vertical accuracy of the processed Control Point coordinates. Typically about 1.5 times worse than horizontal.',
      sources:['Stage 2 Control Point coordinate report'],
      grades:[
       {l:'Survey-grade',r:'Under 2 cm',     s:100},
       {l:'Engineering', r:'Under 5 cm',     s:88},
       {l:'Mapping',     r:'Under 10 cm',    s:65},
       {l:'Coarse',      r:'10 cm or worse', s:35}
      ],
      rec:null, alert:null},
     {name:'Processing Method',
      desc:'How the Control Point coordinates were computed.',
      sources:['Stage 2 Control Point coordinate report'],
      grades:[
       {l:'PPK+CORS',  r:'PPK processed against CORS base',   s:100},
       {l:'CORS',      r:'CORS direct',                       s:90},
       {l:'Provided',  r:'Customer pre-surveyed with accuracy',s:75},
       {l:'Unstated',  r:'Method not stated',                 s:50, flag:'PROCESSING_METHOD_UNSTATED'}
      ],
      rec:null, alert:null}
   ]}
];

// Overall Control Point score under Path 1 Phase 1:
// 0.35 * device_recording + 0.25 * session_quality + 0.40 * layout
function computeGcpOverall(){
  var dr=GCP_BBS.find(function(b){return b.id==='devrec';}).score;
  var ss=GCP_BBS.find(function(b){return b.id==='session';}).score;
  var ly=GCP_BBS.find(function(b){return b.id==='layout';}).score;
  return Math.round(0.35*dr + 0.25*ss + 0.40*ly);
}
var GCP_OVERALL_SCORE = computeGcpOverall();

var GCP_TREND = {
  gcp:[
    {sid:'S-038', date:'14 Oct 2025', score:82, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:85, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:78, anom:true, note:'One device failed mid-flight'},
    {sid:'S-041', date:'05 Dec 2025', score:86, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:89, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:87, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:84, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:88, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:90, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:GCP_OVERALL_SCORE, anom:false}
  ],
  devrec:[
    {sid:'S-038', date:'14 Oct 2025', score:87, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:90, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:78, anom:true, note:'Device 4 had a critical gap'},
    {sid:'S-041', date:'05 Dec 2025', score:91, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:93, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:91, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:89, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:92, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:94, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:91, anom:false}
  ],
  session:[
    {sid:'S-038', date:'14 Oct 2025', score:80, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:82, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:75, anom:false},
    {sid:'S-041', date:'05 Dec 2025', score:83, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:86, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:84, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:82, anom:false},
    {sid:'S-045', date:'14 Feb 2026', score:85, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:87, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:84, anom:false}
  ],
  layout:[
    {sid:'S-038', date:'14 Oct 2025', score:80, anom:false},
    {sid:'S-039', date:'02 Nov 2025', score:84, anom:false},
    {sid:'S-040', date:'18 Nov 2025', score:80, anom:false},
    {sid:'S-041', date:'05 Dec 2025', score:84, anom:false},
    {sid:'S-042', date:'22 Dec 2025', score:88, anom:false},
    {sid:'S-043', date:'10 Jan 2026', score:86, anom:false},
    {sid:'S-044', date:'29 Jan 2026', score:82, anom:true, note:'One target unconfirmed'},
    {sid:'S-045', date:'14 Feb 2026', score:86, anom:false},
    {sid:'S-046', date:'06 Mar 2026', score:88, anom:false},
    {sid:'S-047', date:'28 Mar 2026', score:82, anom:true, note:'One target unconfirmed'}
  ],
  coord:[]
};

var GCP_FLEET_MEDIAN = {
  gcp    :[80,82,79,84,86,85,83,85,87,86],
  devrec :[86,88,84,90,91,90,89,91,92,91],
  session:[80,82,80,83,85,84,82,84,85,84],
  layout :[78,80,78,82,84,83,82,84,86,84],
  coord  :[]
};

var GCP_STATE = {
  selectedBB:null,
  fleetCompare:false
};

function gcpGradeFor(score){
  if(score==null) return {name:'Pending', cls:'dr-gr-marginal'};
  if(score>=90) return {name:'Gold',     cls:'dr-gr-gold'};
  if(score>=75) return {name:'Silver',   cls:'dr-gr-silver'};
  if(score>=60) return {name:'Bronze',   cls:'dr-gr-bronze'};
  if(score>=40) return {name:'Marginal', cls:'dr-gr-marginal'};
  return {name:'Poor', cls:'dr-gr-poor'};
}
function gcpScoreColour(s){
  if(s==null) return 'rgba(255,255,255,.5)';
  if(s>=90) return '#4db896';
  if(s>=75) return '#5596cc';
  if(s>=60) return '#c4882a';
  return '#b84444';
}

// ============================================================
// Control Point page rendering
// ============================================================
function buildGcpPage(){
  buildGcpRibbon();
  selectGcpBB(null);
  drawGcpTrend();
}

function buildGcpRibbon(){
  var ribL=document.getElementById('gc-ribbon-l');
  var ribR=document.getElementById('gc-ribbon-r');
  if(!ribL || !ribR) return;

  // Split BBs into left and right rails. For Control Point (4 BBs): 2 on each side.
  var splitIndex = Math.ceil(GCP_BBS.length / 2);
  var leftBBs  = GCP_BBS.slice(0, splitIndex);
  var rightBBs = GCP_BBS.slice(splitIndex);

  function bbRowHtml(bb){
    var st=bb.pending ? 'neutral' : droneStateFor(bb.score);
    var dotCls = bb.pending ? '' : 'dot-'+st;
    var scoreCls = bb.pending ? '' : 'state-'+st;
    var displayScore = bb.pending ? '—' : bb.score;
    var displayPct = bb.pending
      ? '<span style="font-size:.55em;color:rgba(200,215,228,.42);font-weight:500;letter-spacing:.08em;text-transform:uppercase;margin-left:6px;">pending</span>'
      : '<span style="font-size:.55em;font-weight:700;opacity:.45;vertical-align:super;line-height:0;">%</span>';
    return '<div class="dr-bb'+(bb.pending?' is-pending':'')+'" id="gc-bb-'+bb.id+'" onclick="onGcpBBClick(\''+bb.id+'\')">'
      +(bb.anomaly?'<div class="dr-bb-anom" title="Active alert"></div>':'')
      +'<button class="dr-bb-clear" onclick="event.stopPropagation(); clearGcpBBSelection();" title="Clear selection" aria-label="Clear selection">'
      +  '<svg viewBox="0 0 10 10" fill="none"><path d="M2 2L8 8M8 2L2 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>'
      +'</button>'
      +'<div class="dr-bb-head">'
      +  '<div class="dr-bb-name">'+bb.name+'</div>'
      +  '<div class="dr-bb-cluster">'
      +    (bb.pending ? '' : '<span class="dr-bb-dot '+dotCls+'"></span>')
      +    '<span class="dr-bb-score '+scoreCls+'">'+displayScore+displayPct+'</span>'
      +  '</div>'
      +'</div>'
      +'<div class="dr-bb-activelbl">Active filter</div>'
      +'</div>';
  }

  ribL.innerHTML = leftBBs.map(bbRowHtml).join('');
  ribR.innerHTML = rightBBs.map(bbRowHtml).join('');

  // Hover preview: hovering a BB card temporarily highlights the matching
  // part of the Control Point target hero image. Leaving restores the persistent selection.
  GCP_BBS.forEach(function(bb){
    var card = document.getElementById('gc-bb-'+bb.id);
    if(!card) return;
    card.addEventListener('mouseenter', function(){
      setGcpIllustHighlight(bb.id);
    });
    card.addEventListener('mouseleave', function(){
      setGcpIllustHighlight(GCP_STATE.selectedBB);
    });
  });
}

function onGcpBBClick(bbId){
  if(GCP_STATE.selectedBB === bbId){
    clearGcpBBSelection();
  } else {
    selectGcpBB(bbId);
    openGcpBBPanel(bbId);
  }
}

function clearGcpBBSelection(){
  selectGcpBB(null);
  if(panelMode === 'bb'){
    closeDetail();
  }
}

function selectGcpBB(bbId){
  GCP_STATE.selectedBB = bbId;
  document.querySelectorAll('#gc-ribbon .dr-bb').forEach(function(el){el.classList.remove('active');});
  if(bbId){
    var el=document.getElementById('gc-bb-'+bbId);
    if(el) el.classList.add('active');
  }
  // Light up the matching physical part of the Control Point target hero image.
  setGcpIllustHighlight(bbId);
  updateGcpHero();
  drawGcpTrend();
}

// Apply or clear the Control Point hero part highlight. null clears all.
function setGcpIllustHighlight(bbId){
  var illust = document.getElementById('gc-illust');
  if(!illust) return;
  illust.classList.remove('highlight-devrec','highlight-session','highlight-layout','highlight-coord');
  if(bbId) illust.classList.add('highlight-'+bbId);
}

function updateGcpHero(){
  var tag=document.getElementById('gc-hero-tag');
  var sc=document.getElementById('gc-hero-score');
  var gr=document.getElementById('gc-hero-grade');

  // Recommendations and alerts have moved to the right panel.
  var overallGr = gcpGradeFor(GCP_OVERALL_SCORE);
  tag.textContent = 'Control Point Score';
  sc.innerHTML = GCP_OVERALL_SCORE + '<span class="dr-hero-pct">%</span>';
  gr.textContent = overallGr.name + ' Grade';
}

function openGcpBBPanel(bbId){
  var bb=GCP_BBS.find(function(b){return b.id===bbId;});
  if(!bb) return;
  panelMode='bb';
  buildGcpPanel(bb);
  document.getElementById('detail-panel').classList.add('open');
  document.body.classList.add('panel-open');
}

function buildGcpPanel(bb){
  var col = bb.pending ? 'rgba(255,255,255,.5)' : gcpScoreColour(bb.score);

  document.getElementById('dp-chip').textContent='Control Point Building Block';
  document.getElementById('dp-pname').textContent=bb.name;

  var inds=bb.indicators||[];

  // Triage classification — skipped for pending BBs
  var classified = bb.pending ? [] : inds.map(function(ind){ return {ind:ind, info:classifyIndicator(ind)}; });
  var review  = classified.filter(function(c){ return c.info.needsReview; });
  var passing = classified.filter(function(c){ return !c.info.needsReview; });
  review.sort(function(a,b){
    var stOrder = {crit:0, warn:1, good:2, neutral:3};
    var d = stOrder[a.info.state] - stOrder[b.info.state];
    if(d!==0) return d;
    var as = (a.info.score==null) ? 999 : a.info.score;
    var bs = (b.info.score==null) ? 999 : b.info.score;
    return as - bs;
  });

  // Sub-line
  var gsubEl = document.getElementById('dp-gsub');
  if(bb.pending){
    var pendingMsg = 'Pending Stage 2';
    if(bb.aggregation) pendingMsg += ' \u00b7 ' + bb.aggregation;
    pendingMsg += ' \u00b7 ' + inds.length + ' indicator'+(inds.length===1?'':'s')+' will resolve';
    gsubEl.textContent = pendingMsg;
    gsubEl.style.display = '';
  } else if(passing.length && review.length){
    var sub = inds.length + ' indicators \u00b7 '
            + passing.length + ' passing \u00b7 '
            + review.length + ' need'+(review.length===1?'s':'')+' review';
    if(bb.aggregation) sub = bb.aggregation + ' \u00b7 ' + sub;
    gsubEl.textContent = sub;
    gsubEl.style.display = '';
  } else if(bb.aggregation) {
    // No mix, but aggregation method itself carries useful info (e.g. "Mean of Control Point residuals")
    gsubEl.textContent = bb.aggregation;
    gsubEl.style.display = '';
  } else {
    gsubEl.textContent = '';
    gsubEl.style.display = 'none';
  }

  var bs=document.getElementById('dp-bscore');
  if(bb.pending){
    bs.innerHTML='&mdash;<span class="dp-bpct" style="font-size:.4em;letter-spacing:.06em;">PENDING</span>';
  } else {
    bs.innerHTML=bb.score+'<span class="dp-bpct">%</span>';
  }
  bs.style.color=col;

  // Hero status text dropped — score colour + section headers carry the state
  var statusEl = document.getElementById('dp-status');
  statusEl.textContent = '';
  statusEl.style.display = 'none';

  var tabs='';
  GCP_BBS.forEach(function(b2){
    var c2 = b2.pending ? 'rgba(255,255,255,.4)' : gcpScoreColour(b2.score);
    var sc2 = b2.pending ? '&mdash;' : b2.score+'%';
    tabs+='<div class="dp-tab'+(b2.id===bb.id?' active':'')+'" onclick="switchGcpBBPanel(\''+b2.id+'\')">'
      +'<div class="dp-tab-dot" style="background:'+c2+'"></div>'+b2.name
      +' <span style="font-weight:700;color:'+c2+';margin-left:5px;">'+sc2+'</span></div>';
  });
  document.getElementById('dp-tabs').innerHTML=tabs;

  // ===== BODY =====
  var body='';
  if(bb.pending){
    body+='<div class="dp-sec">Pending</div>'
      +'<div class="bb-rule">'
      +'<div class="bb-rule-lbl">Stage 2 Step C deferred</div>'
      +'<div class="bb-rule-body">This building block resolves once Stage 2 Step C (Control Point Coordinate Processing) completes. The indicators below describe what will be measured.</div>'
      +'</div>';
    body+='<div class="dp-sec">Indicators <span class="dp-sec-count">'+inds.length+'</span></div>';
    if(!inds.length){
      body+='<div class="ind-empty">No indicators defined.</div>';
    } else {
      inds.forEach(function(ind){
        body+=renderIndicatorCard(ind, /*autoExpand=*/false);
      });
    }
  } else {
    if(!inds.length){
      body+='<div class="ind-empty">No indicators defined.</div>';
    } else {
      if(review.length){
        body+='<div class="dp-sec dp-sec-review">Needs Review <span class="dp-sec-count">'+review.length+'</span></div>';
        review.forEach(function(c){
          body+=renderIndicatorCard(c.ind, /*autoExpand=*/true);
        });
      }
      if(passing.length){
        var passHeader = review.length ? 'Passing' : 'Indicators';
        body+='<div class="dp-sec">'+passHeader+' <span class="dp-sec-count">'+passing.length+'</span></div>';
        passing.forEach(function(c){
          body+=renderIndicatorCard(c.ind, /*autoExpand=*/false);
        });
      }
    }
  }
  document.getElementById('dp-body').innerHTML=body;
}

function switchGcpBBPanel(bbId){
  var bb=GCP_BBS.find(function(b){return b.id===bbId;});
  if(bb){ selectGcpBB(bbId); buildGcpPanel(bb); }
}

function toggleGcpFleetCompare(){
  GCP_STATE.fleetCompare = !GCP_STATE.fleetCompare;
  document.getElementById('gc-trend-fleet').classList.toggle('on', GCP_STATE.fleetCompare);
  drawGcpTrend();
}

function drawGcpTrend(){
  var svg=document.getElementById('gc-trend-svg');
  if(!svg) return;
  var key = GCP_STATE.selectedBB || 'gcp';
  var data = GCP_TREND[key] || [];
  var fleetData = GCP_FLEET_MEDIAN[key] || [];

  var bb = GCP_STATE.selectedBB ? GCP_BBS.find(function(b){return b.id===GCP_STATE.selectedBB;}) : null;
  document.getElementById('gc-trend-tag').textContent = bb ? (bb.name + ' Trend') : 'Control Point Score Trend';

  if(!data.length){
    svg.innerHTML = '<text x="440" y="90" text-anchor="middle" fill="rgba(255,255,255,.45)" font-family="IBM Plex Mono" font-size="11" letter-spacing=".06em">Trend resolves after Stage 2 Step C completes</text>';
    return;
  }

  var W=880, H=180, padL=44, padR=20, padT=14, padB=30;
  var innerW=W-padL-padR, innerH=H-padT-padB;
  var n=data.length;
  var sx=function(i){return padL + (n>1?i/(n-1)*innerW:innerW/2);};
  var minScore=40, maxScore=100;
  var sy=function(s){return padT + (1 - (s-minScore)/(maxScore-minScore))*innerH;};

  var s='';
  [40,60,80,100].forEach(function(y){
    s+='<line class="dr-tg-axis" x1="'+padL+'" y1="'+sy(y)+'" x2="'+(W-padR)+'" y2="'+sy(y)+'"/>';
    s+='<text class="dr-tg-tick" x="'+(padL-7)+'" y="'+(sy(y)+3)+'" text-anchor="end">'+y+'</text>';
  });
  s+='<rect class="dr-tg-band" x="'+padL+'" y="'+sy(100)+'" width="'+innerW+'" height="'+(sy(85)-sy(100))+'"/>';

  var pathArea='M '+padL+' '+sy(minScore);
  data.forEach(function(d,i){ pathArea += ' L '+sx(i)+' '+sy(d.score); });
  pathArea += ' L '+(W-padR)+' '+sy(minScore)+' Z';
  s+='<path class="dr-tg-area" d="'+pathArea+'"/>';

  if(GCP_STATE.fleetCompare && fleetData.length){
    var fleetPath='';
    fleetData.forEach(function(v,i){ fleetPath += (i===0?'M ':' L ')+sx(i)+' '+sy(v); });
    s+='<path class="dr-tg-fleet" d="'+fleetPath+'"/>';
    var lastX=sx(fleetData.length-1);
    var lastY=sy(fleetData[fleetData.length-1]);
    s+='<text class="dr-tg-lbl" x="'+(lastX+6)+'" y="'+(lastY+3)+'" fill="rgba(255,255,255,.45)">fleet median</text>';
  }

  var path='';
  data.forEach(function(d,i){ path += (i===0?'M ':' L ')+sx(i)+' '+sy(d.score); });
  s+='<path class="dr-tg-line" d="'+path+'"/>';

  data.forEach(function(d,i){
    var x=sx(i), y=sy(d.score);
    var cls = d.anom ? 'dr-tg-pt anom' : 'dr-tg-pt';
    s+='<circle class="'+cls+'" cx="'+x+'" cy="'+y+'" r="4">'
      +'<title>'+d.sid+' \u00b7 '+d.date+' \u00b7 Score '+d.score+(d.note?'  ('+d.note+')':'')+'</title>'
      +'</circle>';
    if(i%2===0 || i===n-1){
      s+='<text class="dr-tg-tick" x="'+x+'" y="'+(H-padB+16)+'" text-anchor="middle">'+d.date.replace(/ 20\d\d/,'').trim()+'</text>';
    }
  });

  var lastIdx=n-1;
  var lx=sx(lastIdx), ly=sy(data[lastIdx].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(0,180,216,.5)" stroke-width=".7"/>';
  s+='<text class="dr-tg-lbl" x="'+(lx-6)+'" y="'+(ly-12)+'" text-anchor="end" fill="var(--acc)">current</text>';

  svg.innerHTML=s;
}

// ============================================================
// PROOF VIEW -- artefact repository (no scores/recs)
// ============================================================
// Structure: sections of stages. Each section contains groups of files.
// Files reused/extended from DELIVERABLES + supporting docs per stage.
var PROOF_SECTIONS=[
  {section:'Deliverables', desc:'Primary outputs from the pipeline.', groups:[
    {stage:'Capture', items:[
      {fn:'pitpack4_geotagged_images.zip', type:'JPEG+EXIF', size:'2.84 GB', when:'28 Mar 26 09:42', desc:'2,841 frames with embedded GPS/IMU metadata'},
      {fn:'pitpack4_gcps.csv',              type:'CSV',       size:'4.2 KB',  when:'28 Mar 26 09:18', desc:'12 ground control points, field-measured'},
      {fn:'pitpack4_checkpoints.csv',       type:'CSV',       size:'2.1 KB',  when:'28 Mar 26 09:24', desc:'6 independent check points'}
    ]},
    {stage:'Processing', items:[
      {fn:'pitpack4_ortho_4.8cm.tif',       type:'GeoTIFF',   size:'4.1 GB',  when:'28 Mar 26 13:11', desc:'Cloud Optimised orthomosaic, EPSG:32645'},
      {fn:'pitpack4_dsm_10cm.tif',          type:'GeoTIFF',   size:'1.8 GB',  when:'28 Mar 26 13:42', desc:'Digital Surface Model, Float32'},
      {fn:'pitpack4_dtm_10cm.tif',          type:'GeoTIFF',   size:'1.6 GB',  when:'28 Mar 26 13:58', desc:'Digital Terrain Model, ground classified'},
      {fn:'pitpack4_pointcloud.laz',        type:'LAZ',       size:'3.4 GB',  when:'28 Mar 26 13:32', desc:'148M classified points, LAS 1.4'},
      {fn:'pitpack4_mesh.obj',              type:'OBJ+MTL',   size:'2.2 GB',  when:'28 Mar 26 14:08', desc:'Textured 3D mesh, 42M triangles'}
    ]},
    {stage:'Analytics', items:[
      {fn:'pitpack4_stockpile_report.pdf',  type:'PDF',       size:'1.2 MB',  when:'28 Mar 26 15:20', desc:'5 stockpiles, total 584 m^3'},
      {fn:'pitpack4_pits_report.pdf',       type:'PDF',       size:'2.8 MB',  when:'28 Mar 26 15:24', desc:'Pit boundaries, depths, slope angles'},
      {fn:'pitpack4_waste_dumps_report.pdf',type:'PDF',       size:'1.6 MB',  when:'28 Mar 26 15:28', desc:'2 dumps, total 701 m^3'},
      {fn:'pitpack4_cutfill_report.pdf',    type:'PDF',       size:'1.9 MB',  when:'28 Mar 26 15:31', desc:'Cut/fill against 27 Feb 26 baseline'},
      {fn:'pitpack4_stockpile_volumes.xlsx',type:'XLSX',      size:'48 KB',   when:'28 Mar 26 15:21', desc:'Per-stockpile volumes and grades'},
      {fn:'pitpack4_pit_design.dxf',        type:'DXF',       size:'612 KB',  when:'28 Mar 26 15:25', desc:'Pit boundaries as CAD vector'}
    ]}
  ]},
  {section:'Supporting Documents', desc:'Reports, metadata, logs, and reference files generated alongside deliverables.', groups:[
    {stage:'Capture', items:[
      {fn:'pitpack4_flight_log.csv',         type:'LOG',       size:'186 KB', when:'28 Mar 26 10:04', desc:'Telemetry: position, attitude, battery'},
      {fn:'pitpack4_mission_plan.json',      type:'JSON',      size:'14 KB',  when:'28 Mar 26 08:50', desc:'Flight plan: waypoints, altitude, overlap'},
      {fn:'pitpack4_base_station_obs.rinex', type:'RINEX 3.04',size:'42 MB',  when:'28 Mar 26 10:12', desc:'GNSS observation file, 5 hr static session'},
      {fn:'pitpack4_capture_metadata.json',  type:'METADATA',  size:'8.4 KB', when:'28 Mar 26 10:06', desc:'Drone serial, sensor, calibration, weather'},
      {fn:'pitpack4_field_notes.pdf',        type:'PDF',       size:'820 KB', when:'28 Mar 26 11:30', desc:'Field surveyor notes, photos, anomalies'},
      {fn:'pitpack4_capture_qc.pdf',         type:'QC REPORT', size:'1.4 MB', when:'28 Mar 26 11:42', desc:'Capture-stage quality check: overlap, GSD, sun'}
    ]},
    {stage:'Processing', items:[
      {fn:'pitpack4_processing_report.pdf',   type:'PROC REPORT',size:'4.2 MB', when:'28 Mar 26 14:15', desc:'Pipeline log, tie points, residuals, errors'},
      {fn:'pitpack4_quality_report.pdf',      type:'QC REPORT',  size:'2.1 MB', when:'28 Mar 26 14:22', desc:'RMSE, classification confidence, completeness'},
      {fn:'pitpack4_camera_calibration.xml',  type:'XML',        size:'6.1 KB', when:'28 Mar 26 13:02', desc:'Self-calibrated intrinsics, principal point'},
      {fn:'pitpack4_tiepoints.txt',           type:'TXT',        size:'18 MB',  when:'28 Mar 26 13:08', desc:'2.4M tie points, reprojection errors'},
      {fn:'pitpack4_processing_log.log',      type:'LOG',        size:'4.8 MB', when:'28 Mar 26 14:14', desc:'Full pipeline stdout/stderr'},
      {fn:'pitpack4_proc_metadata.json',      type:'METADATA',   size:'12 KB',  when:'28 Mar 26 14:16', desc:'Pipeline versions, parameters, runtime'},
      {fn:'pitpack4_validation_residuals.csv',type:'VALIDATION', size:'9.4 KB', when:'28 Mar 26 14:18', desc:'Check-point validation residuals XYZ'}
    ]},
    {stage:'Analytics', items:[
      {fn:'pitpack4_analytics_log.log',       type:'LOG',        size:'1.1 MB', when:'28 Mar 26 15:32', desc:'Detection, classification, volumetric runs'},
      {fn:'pitpack4_change_detection_export.csv',type:'EXPORT',  size:'68 KB',  when:'28 Mar 26 15:30', desc:'Per-polygon change vs. 27 Feb 26'},
      {fn:'pitpack4_stockpile_export.geojson',type:'GEOJSON',    size:'94 KB',  when:'28 Mar 26 15:22', desc:'Stockpile footprints with attributes'},
      {fn:'pitpack4_aoi_boundary.kml',        type:'KML',        size:'8.2 KB', when:'28 Mar 26 09:00', desc:'Project area of interest, surveyor-defined'},
      {fn:'pitpack4_mine_plan_reference.dxf', type:'REFERENCE',  size:'1.4 MB', when:'28 Mar 26 09:00', desc:'Client mine plan for compliance checks'},
      {fn:'pitpack4_volume_method_note.pdf',  type:'REFERENCE',  size:'420 KB', when:'28 Mar 26 15:23', desc:'TIN vs. lowest-elevation method comparison'}
    ]}
  ]}
];

var PROOF_FILTER='all';
var PROOF_QUERY='';
// Status decisions made on Deliverables page; supporting docs default to pending
// Keyed by filename. Values: 'accept' | 'hold' | 'reject' | 'pending' (default)
var PROOF_STATUS={
  // Capture deliverables
  'pitpack4_geotagged_images.zip':'accept',
  'pitpack4_gcps.csv':'hold',
  'pitpack4_checkpoints.csv':'accept',
  // Processing deliverables
  'pitpack4_ortho_4.8cm.tif':'accept',
  'pitpack4_dsm_10cm.tif':'accept',
  'pitpack4_dtm_10cm.tif':'hold',
  'pitpack4_pointcloud.laz':'accept',
  'pitpack4_mesh.obj':'hold',
  // Analytics deliverables
  'pitpack4_stockpile_report.pdf':'accept',
  'pitpack4_pits_report.pdf':'accept',
  'pitpack4_waste_dumps_report.pdf':'hold',
  'pitpack4_cutfill_report.pdf':'reject'
  // anything else falls through to 'pending'
};

var STATUS_LABEL={accept:'Accepted', hold:'On Hold', reject:'Rejected', pending:'Pending'};

function setProofChip(btn){
  document.querySelectorAll('.pf-chip').forEach(function(c){c.classList.remove('active');});
  btn.classList.add('active');
  PROOF_FILTER=btn.getAttribute('data-stage');
  renderProof();
}

function renderProof(){
  PROOF_QUERY=(document.getElementById('pf-q').value||'').toLowerCase().trim();
  var root=document.getElementById('pf-sections');
  var total=0, visible=0;
  var html='';

  PROOF_SECTIONS.forEach(function(sec){
    if(PROOF_FILTER==='Supporting' && sec.section!=='Supporting Documents') return;
    var stageChip=(['Capture','Processing','Analytics'].indexOf(PROOF_FILTER)>=0)?PROOF_FILTER:null;

    var rowsHtml='';
    var sectionTotal=0;
    sec.groups.forEach(function(grp){
      total+=grp.items.length;
      if(stageChip && grp.stage!==stageChip) return;
      var groupRows=grp.items.filter(function(it){
        if(!PROOF_QUERY) return true;
        return (it.fn+' '+it.type+' '+it.desc).toLowerCase().indexOf(PROOF_QUERY)>=0;
      });
      if(!groupRows.length) return;
      sectionTotal+=groupRows.length;
      visible+=groupRows.length;
      rowsHtml+='<tr class="pf-grp"><td colspan="6">'+grp.stage+'<span class="pf-grp-count">'+groupRows.length+' file'+(groupRows.length===1?'':'s')+'</span></td></tr>';
      groupRows.forEach(function(it){
        var st=PROOF_STATUS[it.fn]||'pending';
        var fnEsc=it.fn.replace(/'/g,"\\'");
        rowsHtml+='<tr class="pf-row">'
          +'<td><div class="pf-fn">'+it.fn+'</div><div class="pf-fn-desc">'+it.desc+'</div></td>'
          +'<td><span class="pf-type">'+it.type+'</span></td>'
          +'<td><span class="pf-size">'+it.size+'</span></td>'
          +'<td><span class="pf-when">'+it.when+'</span></td>'
          +'<td style="text-align:center;"><span class="pf-spill '+st+'">'+STATUS_LABEL[st]+'</span></td>'
          +'<td><button class="pf-dl" title="Download" onclick="downloadProof(\''+fnEsc+'\')">'
          +'<svg viewBox="0 0 14 14" fill="none"><path d="M7 1.5v8M3.5 6.5L7 10l3.5-3.5M2 12h10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
          +'</button></td>'
          +'</tr>';
      });
    });
    if(sectionTotal===0) return;
    html+='<div class="pf-section">'
      +'<div class="pf-shead"><div class="pf-sname">'+sec.section+'</div><div class="pf-scount">'+sectionTotal+' file'+(sectionTotal===1?'':'s')+'</div><div class="pf-srule"></div></div>'
      +'<table class="pf-table">'
      +'<thead><tr><th>File</th><th>Type</th><th>Size</th><th>Generated</th><th>Status</th><th>Download</th></tr></thead>'
      +'<tbody>'+rowsHtml+'</tbody></table>'
      +'</div>';
  });

  if(!visible){
    html='<div class="pf-empty">No artefacts match the current filter.</div>';
  }
  root.innerHTML=html;
  document.getElementById('pf-total').textContent=visible+' of '+total+' files';
}

function downloadProof(fn){
  console.log('Download requested:',fn);
}

// ============================================================
// LOAD VIEW -- input file repository, grouped by system
// ============================================================
var LOAD_SYSTEMS=[
  {sys:'External User Inputs', items:[
    {name:'Coordinate Reference System',  fmt:'WKT / EPSG code / PRJ',           id:'crs'},
    {name:'Boundary',                     fmt:'KML / SHP / GeoJSON',             id:'boundary'}
  ]},
  {sys:'Drone', items:[
    {name:'Raw Images Folder',            fmt:'JPEG (folder upload)',            id:'raw_images'},
    {name:'MRK TimeMark File',            fmt:'MRK / TXT',                       id:'mrk'},
    {name:'Rover RINEX Observation File', fmt:'RINEX 3.x (.obs / .yyo)',         id:'rover_rinex'},
    {name:'Drone User Form',              fmt:'JSON (form.json)',                id:'drone_user_input'},
    {name:'Camera Calibration File',      fmt:'XML / TXT',                       id:'cam_calib'}
  ]},
  {sys:'Base Station', items:[
    {name:'Base RINEX Folder',            fmt:'RINEX 3.x folder (.obs / .yyo)',  id:'base_rinex'},
    {name:'User Input / Antenna Setup Folder', fmt:'JSON / PDF / TXT folder',    id:'ant_setup'},
    {name:'Operator / Session Log Folder', fmt:'operation_log folder',           id:'anchor_session'},
    {name:'RTK Broadcast Data',           fmt:'RTCM (RTK only)',                 id:'rtk_broadcast', optional:true}
  ]},
  {sys:'Control Point', items:[
    {name:'Control Point Points Root Folder',       fmt:'gcp_rinex_point_* folder',        id:'gcp_rinex'},
    {name:'Control Point Layout Record',            fmt:'PDF / DXF / KML',                 id:'gcp_layout', optional:true},
    {name:'Control Point Coordinate File',          fmt:'CSV (id, X, Y, Z)',               id:'gcp_coords', optional:true}
  ]},
  {sys:'Check Point', items:[
    {name:'Check Point RTK Points Root Folder',     fmt:'checkpoint_rtk_point_* folder',   id:'checkpoint_points'}
  ]},
  {sys:'Pre-Processing', items:[
    {name:'Emlid Studio PPK Trajectory',  fmt:'POS / TXT',                       id:'emlid_ppk'},
    {name:'RTKLib PPK Solution',          fmt:'POS / TXT',                       id:'rtklib_ppk'},
    {name:'Trimble Business Center PPK Report', fmt:'PDF',                       id:'tbc_ppk'},
    {name:'TBC CORS Network Report',      fmt:'PDF',                             id:'tbc_cors'},
    {name:'Geotagging Report',            fmt:'PDF / CSV',                       id:'geotag_report'},
    {name:'Geotagged Images',             fmt:'JPEG + EXIF (zip)',               id:'geotagged'},
    {name:'Control Point Points CSV File',          fmt:'CSV',                             id:'gcp_pts_csv'},
    {name:'Check Points CSV File',        fmt:'CSV',                             id:'check_pts_csv'}
  ]},
  {sys:'Processing', items:[
    {name:'NodeODM Task JSON',            fmt:'JSON',                            id:'odm_task'},
    {name:'ODM Quality Report',           fmt:'PDF',                             id:'odm_quality'},
    {name:'Control Point Marking File',             fmt:'TXT (image_id, x, y, gcp_id)',    id:'gcp_marking'},
    {name:'PDAL Stats Output',            fmt:'JSON (point cloud)',              id:'pdal_stats'},
    {name:'DSM Raster Metadata',          fmt:'XML / JSON',                      id:'dsm_meta'},
    {name:'DTM Raster Metadata',          fmt:'XML / JSON',                      id:'dtm_meta'},
    {name:'Orthophoto Raster Metadata',   fmt:'XML / JSON',                      id:'ortho_meta'},
    {name:'Point Cloud',                  fmt:'LAS / LAZ',                       id:'pcd_proc'},
    {name:'3D Model',                     fmt:'OBJ + MTL / FBX',                 id:'mesh_proc'},
    {name:'Processing Report',            fmt:'PDF',                             id:'proc_report'}
  ]},
  {sys:'Analytics', items:[
    {name:'Volume Report (Stockpile)',    fmt:'PDF / XLSX',                      id:'vol_stockpile'},
    {name:'Volume Report (Pit)',          fmt:'PDF / XLSX',                      id:'vol_pit'},
    {name:'Volume Report (Waste Dump)',   fmt:'PDF / XLSX',                      id:'vol_dump'},
    {name:'Volume Report (Cut-Fill)',     fmt:'PDF / XLSX',                      id:'vol_cutfill'},
    {name:'Terrain Maps',                 fmt:'GeoTIFF (Slope, Aspect, Hillshade, Contour)', id:'terrain_maps'},
    {name:'Terrain Analysis Output',      fmt:'TER',                             id:'ter_output'},
    {name:'Comparison Surface Data',      fmt:'CMP',                             id:'cmp_data'}
  ]}
];

var LOAD_STATE={};

var LOAD_FILTER='all';
var LOAD_QUERY='';
var LOAD_PENDING_ID=null; // which input is awaiting the file picker
var LOAD_PENDING_IS_FOLDER=false;
var LOAD_JOB_ID=null;
var LOAD_JOB_TIMER=null;
var LOAD_VALIDATING=false;
var LOAD_UPLOAD_CHAIN=Promise.resolve();

function setLoadJobStatus(msg){
  var el=document.getElementById('ld-job');
  if(el) el.textContent=msg||'No backend job';
  console.log('[load]', msg||'No backend job');
}

function setLoadChip(btn){
  document.querySelectorAll('#view-load .pf-chip').forEach(function(c){c.classList.remove('active');});
  btn.classList.add('active');
  LOAD_FILTER=btn.getAttribute('data-sys');
  renderLoad();
}

function loadStatusOf(it){
  if(LOAD_STATE[it.id]) return 'uploaded';
  if(it.optional) return 'optional';
  return 'pending';
}

function triggerLoad(itemId){
  LOAD_PENDING_ID=itemId;
  var item=null;
  LOAD_SYSTEMS.forEach(function(grp){
    grp.items.forEach(function(it){if(it.id===itemId)item=it;});
  });
  LOAD_PENDING_IS_FOLDER=!!(item && (/folder/i.test(item.fmt||'') || ['base_rinex','ant_setup','anchor_session','gcp_rinex','checkpoint_points','raw_images','rover_rinex','mrk'].indexOf(item.id)>=0));
  var picker=document.getElementById(LOAD_PENDING_IS_FOLDER?'ld-folderinput':'ld-fileinput');
  if(!picker){
    setLoadJobStatus('Upload picker not found.');
    return;
  }
  picker.value='';
  setLoadJobStatus('Choose files for '+(item?item.name:itemId)+'...');
  picker.click();
}

// quick file size formatter
function fmtBytes(n){
  if(n<1024) return n+' B';
  if(n<1024*1024) return (n/1024).toFixed(1)+' KB';
  if(n<1024*1024*1024) return (n/1024/1024).toFixed(1)+' MB';
  return (n/1024/1024/1024).toFixed(2)+' GB';
}

function withCacheBust(url){
  return url+(url.indexOf('?')===-1?'?':'&')+'_ts='+Date.now();
}

function pollLoadJob(jobId){
  fetch(loopApiUrl('/api/jobs/'+encodeURIComponent(jobId)))
    .then(function(res){
      if(!res.ok) throw new Error('HTTP '+res.status);
      return res.json();
    })
    .then(function(job){
      LOAD_JOB_ID=job.id;
      setLoadJobStatus('Job '+job.id.slice(0,8)+' '+job.status);
      if(job.status==='completed' || job.status==='failed'){
        if(LOAD_JOB_TIMER){clearInterval(LOAD_JOB_TIMER);LOAD_JOB_TIMER=null;}
        LOAD_VALIDATING=false;
        renderLoad();
        if(job.status==='completed'){
          var target=job.target||LOAD_TARGET||'base_station';
          if((target==='base_station'||target==='all') && window.dsBase && window.dsBase.refreshApi) window.dsBase.refreshApi();
          if((target==='drone'||target==='all') && window.dsDrone && window.dsDrone.refreshApi) window.dsDrone.refreshApi();
          if((target==='gcp'||target==='all') && window.dsGcp && window.dsGcp.refreshApi) window.dsGcp.refreshApi();
          if((target==='check_point'||target==='all') && window.dsCp && window.dsCp.refreshApi) window.dsCp.refreshApi();
        }
      }
    })
    .catch(function(err){
      if(LOAD_JOB_TIMER){clearInterval(LOAD_JOB_TIMER);LOAD_JOB_TIMER=null;}
      setLoadJobStatus('Backend job error: '+(err.message||String(err)));
    });
}

function uploadFilesToCurrentJob(selected,inputId,summary){
  var body=new FormData();
  body.append('input_id',inputId||'');
  selected.forEach(function(file){
    body.append('files',file,file.webkitRelativePath || file.name);
  });
  setLoadJobStatus('Uploading to backend...');

  var url=LOAD_JOB_ID?loopApiUrl('/api/jobs/'+encodeURIComponent(LOAD_JOB_ID)+'/files'):loopApiUrl('/api/jobs');
  return fetch(url,{method:'POST',body:body})
    .then(function(res){
      return res.text().then(function(text){
        if(!res.ok) throw new Error(extractLoadError(text) || ('HTTP '+res.status));
        return text?JSON.parse(text):{};
      });
    })
    .then(function(created){
      LOAD_JOB_ID=created.job_id || created.id;
      LOAD_STATE[inputId]=summary;
      setLoadJobStatus('Upload ready · job '+LOAD_JOB_ID.slice(0,8));
      renderLoad();
    })
    .catch(function(err){
      setLoadJobStatus('Upload failed: '+(err.message||String(err)));
    });
}

var LOAD_UPLOAD_BATCH_MAX_BYTES=96*1024*1024;
var LOAD_UPLOAD_BATCH_MAX_FILES=40;

function chunkLoadFiles(files){
  var batches=[],cur=[],bytes=0;
  files.forEach(function(file){
    var size=file.size||0;
    if(cur.length && (cur.length>=LOAD_UPLOAD_BATCH_MAX_FILES || bytes+size>LOAD_UPLOAD_BATCH_MAX_BYTES)){
      batches.push(cur);
      cur=[];
      bytes=0;
    }
    cur.push(file);
    bytes+=size;
  });
  if(cur.length)batches.push(cur);
  return batches;
}

function uploadFilesToCurrentJobBatched(selected,inputId,summary){
  var batches=chunkLoadFiles(selected);
  var chain=Promise.resolve();
  batches.forEach(function(batch,idx){
    chain=chain.then(function(){
      var body=new FormData();
      body.append('input_id',inputId||'');
      batch.forEach(function(file){
        body.append('files',file,file.webkitRelativePath || file.name);
      });
      setLoadJobStatus('Uploading '+inputId+' batch '+(idx+1)+' / '+batches.length+' ('+batch.length+' files)...');
      var url=LOAD_JOB_ID?loopApiUrl('/api/jobs/'+encodeURIComponent(LOAD_JOB_ID)+'/files'):loopApiUrl('/api/jobs');
      return fetch(url,{method:'POST',body:body})
        .then(function(res){
          return res.text().then(function(text){
            if(!res.ok) throw new Error(extractLoadError(text) || ('HTTP '+res.status));
            return text?JSON.parse(text):{};
          });
        })
        .then(function(created){
          LOAD_JOB_ID=created.job_id || created.id;
        });
    });
  });
  return chain.then(function(){
    LOAD_STATE[inputId]=summary;
    setLoadJobStatus('Upload ready - job '+LOAD_JOB_ID.slice(0,8));
    renderLoad();
  }).catch(function(err){
    setLoadJobStatus('Upload failed: '+(err.message||String(err)));
    throw err;
  });
}

function submitLoadFilesToBackend(files,inputId,summary){
  var selected=[];
  for(var i=0;i<files.length;i++) selected.push(files[i]);
  if(!selected.length) return;
  setLoadJobStatus('Queued upload for '+inputId+'...');
  LOAD_UPLOAD_CHAIN=LOAD_UPLOAD_CHAIN.then(function(){
    return uploadFilesToCurrentJobBatched(selected,inputId,summary);
  });
  return LOAD_UPLOAD_CHAIN;
}

function extractLoadError(text){
  if(!text) return '';
  try{
    var doc=new DOMParser().parseFromString(text,'text/html');
    var p=doc.querySelector('p');
    if(p && p.textContent) return p.textContent;
  }catch(e){}
  try{
    var obj=JSON.parse(text);
    return obj.description || obj.error || obj.message || text;
  }catch(e){}
  return text;
}

function loadGroupForSystem(systemName){
  for(var i=0;i<LOAD_SYSTEMS.length;i++){
    if(LOAD_SYSTEMS[i].sys===systemName) return LOAD_SYSTEMS[i];
  }
  return null;
}

function missingRequiredLoadItems(systemName){
  var grp=loadGroupForSystem(systemName);
  if(!grp) return [];
  return grp.items.filter(function(it){
    return !it.optional && !LOAD_STATE[it.id];
  });
}

function validateLoadSystemNow(systemName){
  var targetBySystem={
    'Base Station':'base_station',
    'Drone':'drone',
    'Control Point':'gcp',
    'Check Point':'check_point'
  };
  var target=targetBySystem[systemName];
  if(!target) return;
  if(LOAD_VALIDATING) return;
  if(!LOAD_JOB_ID){
    setLoadJobStatus('Upload '+systemName+' files first.');
    return;
  }
  var missing=missingRequiredLoadItems(systemName);
  if(missing.length){
    setLoadJobStatus('Missing required '+systemName+' input: '+missing.map(function(it){return it.name;}).join(', '));
    return;
  }
  LOAD_VALIDATING=true;
  setLoadJobStatus('Validating '+systemName+'...');
  renderLoad();
  fetch(loopApiUrl('/api/jobs/'+encodeURIComponent(LOAD_JOB_ID)+'/validate'),{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({target:target})
  })
    .then(function(res){
      return res.text().then(function(text){
        if(!res.ok) throw new Error(extractLoadError(text) || ('HTTP '+res.status));
        return text?JSON.parse(text):{};
      });
    })
    .then(function(created){
      setLoadJobStatus('Job '+created.job_id.slice(0,8)+' queued');
      if(LOAD_JOB_TIMER) clearInterval(LOAD_JOB_TIMER);
      pollLoadJob(created.job_id);
      LOAD_JOB_TIMER=setInterval(function(){pollLoadJob(created.job_id);},2500);
    })
    .catch(function(err){
      LOAD_VALIDATING=false;
      renderLoad();
      setLoadJobStatus('Validate failed: '+(err.message||String(err)));
    });
}

function validateLoadSystem(systemName){
  if(LOAD_VALIDATING) return;
  setLoadJobStatus('Waiting for uploads to finish before validating...');
  LOAD_UPLOAD_CHAIN.then(function(){
    validateLoadSystemNow(systemName);
  });
}
window.validateLoadSystem=validateLoadSystem;

function onLoadFileChosen(e){
  var files=e.target.files;
  if(!files || !files.length){
    setLoadJobStatus('No files selected.');
    return;
  }
  if(!LOAD_PENDING_ID){
    setLoadJobStatus('No upload row selected. Click Upload again.');
    return;
  }
  var f=files[0];
  var summary;
  // if multiple selected (e.g. raw images folder), aggregate
  if(files.length>1){
    var total=0; for(var i=0;i<files.length;i++) total+=files[i].size;
    var firstName=f.webkitRelativePath || f.name;
    summary={fn:files.length+' files ('+firstName+', ...)', sz:fmtBytes(total)};
  } else {
    summary={fn:(f.webkitRelativePath || f.name), sz:fmtBytes(f.size)};
  }
  setLoadJobStatus('Selected '+files.length+' file'+(files.length===1?'':'s')+' for '+LOAD_PENDING_ID+'. Uploading...');
  submitLoadFilesToBackend(files,LOAD_PENDING_ID,summary);
  LOAD_PENDING_ID=null;
  LOAD_PENDING_IS_FOLDER=false;
  e.target.value=''; // reset so same file can be picked again
  renderLoad();
}

function renderLoad(){
  LOAD_QUERY=(document.getElementById('ld-q').value||'').toLowerCase().trim();
  var root=document.getElementById('ld-sections');
  var total=0, uploaded=0, visible=0;
  var html='';

  LOAD_SYSTEMS.forEach(function(grp){
    if(LOAD_FILTER!=='all' && grp.sys!==LOAD_FILTER) return;
    var rowsHtml='';
    var sectionVisible=0;
    var sectionUploaded=0;
    var sectionRequired=0;
    var sectionRequiredUploaded=0;
    grp.items.forEach(function(it){
      total++;
      var st=loadStatusOf(it);
      if(st==='uploaded') sectionUploaded++;
      if(!it.optional){
        sectionRequired++;
        if(st==='uploaded') sectionRequiredUploaded++;
      }
      if(LOAD_QUERY){
        var hay=(it.name+' '+it.fmt+' '+(LOAD_STATE[it.id]?LOAD_STATE[it.id].fn:'')).toLowerCase();
        if(hay.indexOf(LOAD_QUERY)<0) return;
      }
      sectionVisible++;
      visible++;
      var s=LOAD_STATE[it.id];
      var statusLbl=st==='uploaded'?'Uploaded':st==='optional'?'Optional':'Pending';
      var upLbl=s?'Replace':'Upload';
      var upCls=s?'ld-up replace':'ld-up';
      var fileCell=s
        ? '<div class="ld-fn">'+s.fn+'</div><div class="ld-fn-sz">'+s.sz+'</div>'
        : '<div class="ld-fn-empty">'+(it.optional?'Not required':'No file uploaded')+'</div>';
      rowsHtml+='<tr class="ld-row">'
        +'<td><div class="ld-iname">'+it.name+'</div><div class="ld-iname-fmt">'+it.fmt+'</div></td>'
        +'<td style="text-align:center;"><span class="ld-spill '+st+'">'+statusLbl+'</span></td>'
        +'<td>'+fileCell+'</td>'
      +'<td style="text-align:center;"><button type="button" class="'+upCls+'" data-load-trigger="'+it.id+'">'
        +'<svg viewBox="0 0 10 10" fill="none"><path d="M5 6.5V1.5M3 3L5 1 7 3M1.5 8.4h7" stroke="currentColor" stroke-width=".9" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        +upLbl+'</button></td>'
        +'</tr>';
    });
    if(sectionVisible===0) return;
    var validateDisabled=LOAD_VALIDATING?' disabled':'';
    var validateLabel=LOAD_VALIDATING?'Validating':'Validate';
    var canValidate=['Base Station','Drone','Control Point','Check Point'].indexOf(grp.sys)>=0;
    var validateBtn=canValidate
      ? '<button type="button" class="ld-up" style="margin-left:10px"'+validateDisabled+' data-load-validate="'+grp.sys+'">'+validateLabel+'</button>'
      : '';
    var countText=sectionRequiredUploaded+' / '+sectionRequired+' required uploaded';
    if(sectionUploaded>sectionRequiredUploaded) countText+=' + '+(sectionUploaded-sectionRequiredUploaded)+' optional';
    html+='<div class="ld-section">'
      +'<div class="ld-shead"><div class="ld-sname">'+grp.sys+'</div>'
      +'<div class="ld-scount">'+countText+'</div>'
      +validateBtn+'<div class="ld-srule"></div></div>'
      +'<table class="ld-table">'
      +'<thead><tr><th>Required Input</th><th>Status</th><th>File</th><th>Action</th></tr></thead>'
      +'<tbody>'+rowsHtml+'</tbody></table>'
      +'</div>';
  });

  // count overall uploaded across full data (not just visible)
  uploaded=0;
  LOAD_SYSTEMS.forEach(function(g){g.items.forEach(function(it){if(LOAD_STATE[it.id]) uploaded++;});});

  if(!visible) html='<div class="pf-empty">No inputs match the current filter.</div>';
  root.innerHTML=html;

  document.getElementById('ld-total').textContent=uploaded+' of '+total+' uploaded';
  var pct=total?Math.round(uploaded*100/total):0;
  document.getElementById('ld-prog-fill').style.width=pct+'%';
  document.getElementById('ld-prog-num').textContent=pct+'%';
}

document.addEventListener('click',function(e){
  var trigger=e.target.closest && e.target.closest('[data-load-trigger]');
  if(trigger){
    e.preventDefault();
    triggerLoad(trigger.getAttribute('data-load-trigger'));
    return;
  }
  var validate=e.target.closest && e.target.closest('[data-load-validate]');
  if(validate){
    e.preventDefault();
    validateLoadSystem(validate.getAttribute('data-load-validate'));
  }
});


// ============================================================
// ORIGIN VIEW -- Entity > Location > Site > Instance hierarchy
// ============================================================
// Tree state. Each entity has locations, each location has sites, each site has instances.
// Seeded with one complete chain so Load is unlocked by default (matches existing UX).
var ORIGIN={
  entities:[
    {id:'e1', name:'CrystalBall Aerospace', type:'Operator',
     locations:[
       {id:'l1', name:'Telangana', region:'South India',
        sites:[
          {id:'s1', name:'Pitpack 4', kind:'Iron Ore Mine',
           instances:[
             {id:'i1', label:'28 Mar 2026 Survey', when:'2026-03-28 09:30 IST', notes:'Quarterly DSM + volumetrics'}
           ]}
        ]}
     ]}
  ],
  selected:{entity:'e1', location:'l1', site:'s1', instance:'i1'},
  collapsed:{}, // node id -> bool
  // step is the next-to-create level. 0=entity, 1=location, 2=site, 3=instance, 4=complete
  step:4
};

function findEntity(id){return ORIGIN.entities.find(function(e){return e.id===id;});}
function findLocation(eid,lid){var e=findEntity(eid);return e?e.locations.find(function(l){return l.id===lid;}):null;}
function findSite(eid,lid,sid){var l=findLocation(eid,lid);return l?l.sites.find(function(s){return s.id===sid;}):null;}
function findInstance(eid,lid,sid,iid){var s=findSite(eid,lid,sid);return s?s.instances.find(function(i){return i.id===iid;}):null;}

function originComplete(){
  // at least one full path exists
  for(var i=0;i<ORIGIN.entities.length;i++){
    var e=ORIGIN.entities[i];
    for(var j=0;j<(e.locations||[]).length;j++){
      var l=e.locations[j];
      for(var k=0;k<(l.sites||[]).length;k++){
        var s=l.sites[k];
        if((s.instances||[]).length>0) return true;
      }
    }
  }
  return false;
}

function originStep(){
  // determine which step the user is on based on selected context
  // returns 0 if no entity selected, 1 if entity but no location, etc.
  // step 4 = complete
  var sel=ORIGIN.selected;
  if(!sel.entity || !findEntity(sel.entity)) return 0;
  if(!sel.location || !findLocation(sel.entity,sel.location)) return 1;
  if(!sel.site || !findSite(sel.entity,sel.location,sel.site)) return 2;
  if(!sel.instance || !findInstance(sel.entity,sel.location,sel.site,sel.instance)) return 3;
  return 4;
}

// short unique id
function uid(prefix){return prefix+Date.now().toString(36).slice(-5)+Math.floor(Math.random()*99).toString(36);}

// ---- STEP RENDERING ----
function renderSteps(){
  var root=document.getElementById('or-steps');
  var step=originStep();
  ORIGIN.step=step;
  var sel=ORIGIN.selected;

  var entity   = sel.entity   ? findEntity(sel.entity)                                    : null;
  var location = entity && sel.location ? findLocation(sel.entity,sel.location)           : null;
  var site     = location && sel.site ? findSite(sel.entity,sel.location,sel.site)        : null;
  var instance = site && sel.instance ? findInstance(sel.entity,sel.location,sel.site,sel.instance) : null;

  // build entity step
  var html='';
  html += stepBlock(1,'Entity', step,
    'Organisation, client, or operator that owns the project. Example: a mining company, contractor, or holding firm.',
    null, entity,
    // form
    '<div class="or-frow">'
    +'<div class="or-field"><label class="or-flbl">Entity Name *</label><input class="or-fin" id="or-e-name" placeholder="e.g. CrystalBall Aerospace"/></div>'
    +'<div class="or-field"><label class="or-flbl">Type</label><input class="or-fin" id="or-e-type" placeholder="Operator / Contractor / Client"/></div>'
    +'</div>',
    // existing-picker shows all entities for selection
    pickerHtml('entity', ORIGIN.entities.map(function(e){return {id:e.id, name:e.name, sub:e.type};}), sel.entity),
    'createEntity()', 'Entity'
  );

  // location
  var locItems = entity ? entity.locations.map(function(l){return {id:l.id, name:l.name, sub:l.region};}) : [];
  html += stepBlock(2,'Location', step,
    'Geographic or regional grouping under the Entity. Example: a state, region, or country.',
    entity?{k:'Under Entity',v:entity.name}:null,
    location,
    '<div class="or-frow">'
    +'<div class="or-field"><label class="or-flbl">Location Name *</label><input class="or-fin" id="or-l-name" placeholder="e.g. Telangana"/></div>'
    +'<div class="or-field"><label class="or-flbl">Region</label><input class="or-fin" id="or-l-region" placeholder="South India"/></div>'
    +'</div>',
    pickerHtml('location', locItems, sel.location),
    'createLocation()', 'Location'
  );

  // site
  var siteItems = location ? location.sites.map(function(s){return {id:s.id, name:s.name, sub:s.kind};}) : [];
  html += stepBlock(3,'Site', step,
    'Operational project or site under the Location. Example: an individual mine, quarry, or facility.',
    location?{k:'Under Location',v:location.name+(entity?' / '+entity.name:'')}:null,
    site,
    '<div class="or-frow">'
    +'<div class="or-field"><label class="or-flbl">Site Name *</label><input class="or-fin" id="or-s-name" placeholder="e.g. Pitpack 4"/></div>'
    +'<div class="or-field"><label class="or-flbl">Kind</label><input class="or-fin" id="or-s-kind" placeholder="Iron Ore Mine / Quarry / ..."/></div>'
    +'</div>',
    pickerHtml('site', siteItems, sel.site),
    'createSite()', 'Site'
  );

  // instance
  var instItems = site ? site.instances.map(function(i){return {id:i.id, name:i.label, sub:i.when};}) : [];
  // default datetime: now
  var pad=function(n){return (n<10?'0':'')+n;};
  var now=new Date();
  var nowStr=now.getFullYear()+'-'+pad(now.getMonth()+1)+'-'+pad(now.getDate())+'T'+pad(now.getHours())+':'+pad(now.getMinutes());
  html += stepBlock(4,'Instance', step,
    'Time-based dataset for this site. Example: a survey on a specific date. Required to unlock Load.',
    site?{k:'Under Site',v:site.name+(location?' / '+location.name:'')}:null,
    instance,
    '<div class="or-field"><label class="or-flbl">Instance Label *</label><input class="or-fin" id="or-i-label" placeholder="e.g. 28 Mar 2026 Survey"/></div>'
    +'<div class="or-frow">'
    +'<div class="or-field"><label class="or-flbl">Date &amp; Time</label><input class="or-fin" id="or-i-when" type="datetime-local" value="'+nowStr+'"/></div>'
    +'<div class="or-field"><label class="or-flbl">Notes</label><input class="or-fin" id="or-i-notes" placeholder="Quarterly DSM, volumetrics, ..."/></div>'
    +'</div>',
    pickerHtml('instance', instItems, sel.instance),
    'createInstance()', 'Instance'
  );

  root.innerHTML=html;
}

function stepBlock(num, name, currentStep, desc, ctx, currentValue, formHtml, pickerHtmlStr, createFn, kind){
  var idx=num-1;
  // status: locked if previous step incomplete; active if equal to current step; done otherwise
  var cls;
  if(idx>currentStep) cls='locked';
  else if(idx===currentStep) cls='active';
  else cls='done';
  var hint = (cls==='done' && currentValue) ? 'Selected: '+(currentValue.name||currentValue.label||'') : (cls==='locked' ? 'Locked' : 'Required');
  var inner = '';
  if(cls==='locked'){
    inner = '<div class="or-step-desc" style="margin-bottom:0;color:rgba(255,255,255,.32);">'+desc+'</div>';
  } else {
    inner += '<div class="or-step-desc">'+desc+'</div>';
    if(ctx) inner += '<div class="or-step-ctx"><span class="or-step-ctx-k">'+ctx.k+'</span>'+ctx.v+'</div>';
    inner += formHtml;
    inner += '<div class="or-actions">';
    inner += '<button class="or-btn primary" onclick="'+createFn+'">+ Create '+kind+'</button>';
    inner += '</div>';
    if(pickerHtmlStr) inner += pickerHtmlStr;
  }
  return '<div class="or-step '+cls+'">'
    +'<div class="or-step-head">'
    +'<div class="or-step-num">'+(cls==='done'?'&#10003;':num)+'</div>'
    +'<div class="or-step-name">'+name+'</div>'
    +'<div class="or-step-hint">'+hint+'</div>'
    +'</div>'
    +inner
    +'</div>';
}

function pickerHtml(kind, items, selectedId){
  if(!items || !items.length) return '';
  var lbl={entity:'Existing Entities', location:'Existing Locations', site:'Existing Sites', instance:'Existing Instances'}[kind];
  var pickFn={entity:'pickEntity', location:'pickLocation', site:'pickSite', instance:'pickInstance'}[kind];
  return '<div class="or-existing">'
    +'<div class="or-existing-h">'+lbl+'</div>'
    +'<div class="or-pick">'
    + items.map(function(it){
        var sel=(it.id===selectedId)?' selected':'';
        return '<div class="or-pick-item'+sel+'" onclick="'+pickFn+'(\''+it.id+'\')">'+it.name+(it.sub?' <span style="color:rgba(255,255,255,.3);margin-left:4px;">'+it.sub+'</span>':'')+'</div>';
      }).join('')
    +'</div>'
    +'</div>';
}

// ---- CREATE HANDLERS ----
function createEntity(){
  var nm=document.getElementById('or-e-name').value.trim();
  var tp=document.getElementById('or-e-type').value.trim()||'Operator';
  if(!nm){flashStep('or-e-name');return;}
  var e={id:uid('e'), name:nm, type:tp, locations:[]};
  ORIGIN.entities.push(e);
  ORIGIN.selected={entity:e.id, location:null, site:null, instance:null};
  renderOrigin();
}
function createLocation(){
  var sel=ORIGIN.selected;
  if(!sel.entity) return;
  var nm=document.getElementById('or-l-name').value.trim();
  var rg=document.getElementById('or-l-region').value.trim();
  if(!nm){flashStep('or-l-name');return;}
  var l={id:uid('l'), name:nm, region:rg, sites:[]};
  findEntity(sel.entity).locations.push(l);
  ORIGIN.selected={entity:sel.entity, location:l.id, site:null, instance:null};
  renderOrigin();
}
function createSite(){
  var sel=ORIGIN.selected;
  if(!sel.entity || !sel.location) return;
  var nm=document.getElementById('or-s-name').value.trim();
  var kd=document.getElementById('or-s-kind').value.trim()||'Site';
  if(!nm){flashStep('or-s-name');return;}
  var s={id:uid('s'), name:nm, kind:kd, instances:[]};
  findLocation(sel.entity,sel.location).sites.push(s);
  ORIGIN.selected={entity:sel.entity, location:sel.location, site:s.id, instance:null};
  renderOrigin();
}
function createInstance(){
  var sel=ORIGIN.selected;
  if(!sel.entity || !sel.location || !sel.site) return;
  var lbl=document.getElementById('or-i-label').value.trim();
  var wn=document.getElementById('or-i-when').value;
  var nt=document.getElementById('or-i-notes').value.trim();
  if(!lbl){flashStep('or-i-label');return;}
  var i={id:uid('i'), label:lbl, when:wn||new Date().toISOString().slice(0,16), notes:nt};
  findSite(sel.entity,sel.location,sel.site).instances.push(i);
  ORIGIN.selected={entity:sel.entity, location:sel.location, site:sel.site, instance:i.id};
  renderOrigin();
  // gentle highlight: scroll unlock into view
  setTimeout(function(){document.getElementById('or-unlock').scrollIntoView({behavior:'smooth',block:'nearest'});},80);
}

function flashStep(inputId){
  var el=document.getElementById(inputId); if(!el) return;
  el.style.borderColor='rgba(0,180,216,.7)';
  el.focus();
  setTimeout(function(){el.style.borderColor='';},900);
}

// ---- PICK HANDLERS ----
function pickEntity(id){ORIGIN.selected={entity:id, location:null, site:null, instance:null};renderOrigin();}
function pickLocation(id){var s=ORIGIN.selected;ORIGIN.selected={entity:s.entity, location:id, site:null, instance:null};renderOrigin();}
function pickSite(id){var s=ORIGIN.selected;ORIGIN.selected={entity:s.entity, location:s.location, site:id, instance:null};renderOrigin();}
function pickInstance(id){var s=ORIGIN.selected;ORIGIN.selected={entity:s.entity, location:s.location, site:s.site, instance:id};renderOrigin();}

// ---- TREE RENDERING ----
function renderTree(){
  var root=document.getElementById('or-tree');
  if(!ORIGIN.entities.length){
    root.innerHTML='<div class="or-tree-empty">No entities yet.<br/>Create your first Entity on the left to begin.</div>';
    document.getElementById('or-tree-meta').textContent='0 entities';
    return;
  }
  // counts
  var locC=0,siteC=0,instC=0;
  ORIGIN.entities.forEach(function(e){
    e.locations.forEach(function(l){
      locC++;
      l.sites.forEach(function(s){siteC++; instC+=s.instances.length;});
    });
  });
  document.getElementById('or-tree-meta').textContent=ORIGIN.entities.length+' ent &middot; '+locC+' loc &middot; '+siteC+' site &middot; '+instC+' inst';

  var html='';
  ORIGIN.entities.forEach(function(e){
    html+=renderTreeNode('entity', e.id, e.name, e.type, e.locations.length>0,
      e.locations.map(function(l){
        return renderTreeNode('location', l.id, l.name, l.region, l.sites.length>0,
          l.sites.map(function(s){
            return renderTreeNode('site', s.id, s.name, s.kind, s.instances.length>0,
              s.instances.map(function(i){
                return renderTreeNode('instance', i.id, i.label, i.when, false, []);
              }).join('')
            );
          }).join('')
        );
      }).join('')
    );
  });
  root.innerHTML=html;
}

function renderTreeNode(kind, id, name, sub, hasChildren, childrenHtml){
  var collapsed = ORIGIN.collapsed[kind+':'+id];
  var sel=ORIGIN.selected;
  var selected = (kind==='entity'&&sel.entity===id)
              || (kind==='location'&&sel.location===id)
              || (kind==='site'&&sel.site===id)
              || (kind==='instance'&&sel.instance===id);
  var icon={
    entity:'<svg viewBox="0 0 10 10" fill="none"><rect x="1.5" y="1.5" width="7" height="7" rx="1" stroke="currentColor" stroke-width=".8"/><path d="M3.2 4.2h3.6M3.2 5.7h3.6" stroke="currentColor" stroke-width=".7"/></svg>',
    location:'<svg viewBox="0 0 10 10" fill="none"><path d="M5 1.2c-1.7 0-2.8 1.2-2.8 2.8 0 2 2.8 4.8 2.8 4.8s2.8-2.8 2.8-4.8c0-1.6-1.1-2.8-2.8-2.8z" stroke="currentColor" stroke-width=".8"/><circle cx="5" cy="4" r="1" fill="currentColor"/></svg>',
    site:'<svg viewBox="0 0 10 10" fill="none"><path d="M1.5 8.5L5 2l3.5 6.5H1.5z" stroke="currentColor" stroke-width=".8" stroke-linejoin="round"/></svg>',
    instance:'<svg viewBox="0 0 10 10" fill="none"><circle cx="5" cy="5" r="3.5" stroke="currentColor" stroke-width=".8"/><path d="M5 3v2l1.5 1" stroke="currentColor" stroke-width=".8" stroke-linecap="round"/></svg>'
  }[kind];
  var tag={entity:'ENT', location:'LOC', site:'SITE', instance:'INST'}[kind];
  var clsNode='or-tn'+(collapsed?' collapsed':'');
  var clsRow='or-tn-row'+(selected?' selected':'');
  var pickFn={entity:'pickEntity', location:'pickLocation', site:'pickSite', instance:'pickInstance'}[kind];
  return '<div class="'+clsNode+'">'
    +'<div class="'+clsRow+'">'
    +'<div class="or-tn-chev'+(hasChildren?'':' empty')+'" onclick="toggleTreeNode(\''+kind+'\',\''+id+'\',event)">&#9662;</div>'
    +'<div onclick="'+pickFn+'(\''+id+'\')" style="display:flex;align-items:center;gap:8px;flex:1;">'
    +'<div class="or-tn-icon">'+icon+'</div>'
    +'<div class="or-tn-lbl">'+name+'</div>'
    +'<div class="or-tn-tag">'+tag+'</div>'
    +(sub?'<div class="or-tn-sub">'+sub+'</div>':'')
    +'</div>'
    +'</div>'
    +(hasChildren?'<div class="or-tn-children">'+childrenHtml+'</div>':'')
    +'</div>';
}

function toggleTreeNode(kind,id,e){
  if(e){e.stopPropagation();}
  var k=kind+':'+id;
  ORIGIN.collapsed[k]=!ORIGIN.collapsed[k];
  renderTree();
}

// ---- BREADCRUMB ----
function renderCrumbs(){
  var el=document.getElementById('or-crumbs');
  var sel=ORIGIN.selected;
  var e=sel.entity?findEntity(sel.entity):null;
  var l=e&&sel.location?findLocation(sel.entity,sel.location):null;
  var s=l&&sel.site?findSite(sel.entity,sel.location,sel.site):null;
  var i=s&&sel.instance?findInstance(sel.entity,sel.location,sel.site,sel.instance):null;
  var parts=[];
  parts.push('<span class="or-crumb-lbl">Path</span>');
  if(!e){parts.push('<span class="or-crumb-empty">No entity selected</span>');}
  else{
    parts.push('<span class="or-crumb">'+e.name+'</span>');
    parts.push('<span class="or-crumb-sep">&rsaquo;</span>');
    parts.push(l?'<span class="or-crumb">'+l.name+'</span>':'<span class="or-crumb-empty">Location</span>');
    parts.push('<span class="or-crumb-sep">&rsaquo;</span>');
    parts.push(s?'<span class="or-crumb">'+s.name+'</span>':'<span class="or-crumb-empty">Site</span>');
    parts.push('<span class="or-crumb-sep">&rsaquo;</span>');
    parts.push(i?'<span class="or-crumb">'+i.label+'</span>':'<span class="or-crumb-empty">Instance</span>');
  }
  el.innerHTML=parts.join(' ');
}

// ---- UNLOCK BAR ----
function renderUnlock(){
  var bar=document.getElementById('or-unlock');
  var txt=document.getElementById('or-unlock-text');
  var link=document.getElementById('or-unlock-link');
  if(originComplete()){
    bar.classList.add('ready');
    txt.textContent='Load is unlocked. You can now upload input files.';
    link.classList.remove('disabled');
  } else {
    bar.classList.remove('ready');
    var step=originStep();
    var need=['Create an Entity','Create a Location','Create a Site','Create an Instance'][step]||'Complete the hierarchy';
    txt.textContent=need+' to unlock Load.';
    link.classList.add('disabled');
  }
  // also update nav badge state
  var nbL=document.getElementById('nbadge-load');
  if(nbL) nbL.classList.toggle('locked', !originComplete());
}

function renderOrigin(){
  renderSteps();
  renderTree();
  renderCrumbs();
  renderUnlock();
}

function goToLoad(){
  if(!originComplete()) return;
  switchModule('load');
}

function originGate(targetModule){
  // returns true if allowed, false if blocked (and shows toast)
  if(targetModule==='load' && !originComplete()){
    var t=document.getElementById('or-toast');
    t.classList.add('show');
    setTimeout(function(){t.classList.remove('show');},2600);
    // redirect to origin
    switchModule('origin');
    return false;
  }
  return true;
}


// ============================================================
// VIEW SWITCHING (Confidence/Score, Confidence/Deliverables, Site Reality, Proof, Load, Origin)
// ============================================================
var currentView='score';
var currentModule='conf';  // 'conf' | 'sr' | 'proof' | 'load' | 'origin'

function switchView(v){
  if(currentModule!=='conf'){switchModule('conf');}
  currentView=v;
  var isScore=(v==='score');
  document.getElementById('vtab-score').classList.toggle('active',isScore);
  document.getElementById('vtab-del').classList.toggle('active',!isScore);
  document.getElementById('view-score').style.display=isScore?'':'none';
  document.getElementById('view-del').classList.toggle('show',!isScore);
  CV.style.display=isScore?'':'none';
  // Close panel when switching views away from its source context
  if(isScore && panelMode==='del') closeDetail();
  if(!isScore && panelMode==='bb') closeDetail();
}

function switchModule(m){
  // Gate: Load requires complete Origin
  if(m==='load' && !originComplete()){
    var t=document.getElementById('or-toast');
    if(t){t.classList.add('show'); setTimeout(function(){t.classList.remove('show');},2600);}
    m='origin'; // redirect
  }

  currentModule=m;
  // bnav active state -- deselect all when not in conf/sr/hardware
  var mods=document.querySelectorAll('#bnav .mod');
  mods.forEach(function(el){el.classList.remove('active');});
  if(m==='conf') mods[0].classList.add('active');
  else if(m==='sr') mods[1].classList.add('active');
  else if(m==='drone' || m==='base' || m==='gcp'){
    // Hardware pill is the third .mod (index 2); keep it active for any of its children
    var hwPill=document.getElementById('mod-hw');
    if(hwPill) hwPill.classList.add('active');
  }
  // (proof/origin/load leave all bnav items unselected per spec)

  // Always close hardware popover on navigation
  var hwPill2=document.getElementById('mod-hw');
  if(hwPill2) hwPill2.classList.remove('open');

  // top-right badge active state
  var nbP=document.getElementById('nbadge-proof');
  var nbO=document.getElementById('nbadge-origin');
  var nbL=document.getElementById('nbadge-load');
  if(nbP) nbP.classList.toggle('active',m==='proof');
  if(nbO) nbO.classList.toggle('active',m==='origin');
  if(nbL) nbL.classList.toggle('active',m==='load');
  // Load badge shows locked indicator when Origin incomplete
  if(nbL) nbL.classList.toggle('locked', !originComplete() && m!=='load');

  // view-tabs only meaningful in Confidence
  document.querySelector('.view-tabs').style.display=(m==='conf')?'':'none';

  // dimbar (entity selector) hidden on Origin -- it IS the selector there
  var db=document.getElementById('dimbar');
  if(db) db.style.display=(m==='origin')?'none':'';

  // hide everything first
  document.getElementById('view-score').style.display='none';
  document.getElementById('view-del').classList.remove('show');
  document.getElementById('view-sr').classList.remove('show');
  document.getElementById('view-proof').classList.remove('show');
  document.getElementById('view-load').classList.remove('show');
  document.getElementById('view-origin').classList.remove('show');
  document.getElementById('view-drone').classList.remove('show');
  document.getElementById('view-base').classList.remove('show');
  document.getElementById('view-gcp').classList.remove('show');
  var _vcp=document.getElementById('view-checkpoint'); if(_vcp)_vcp.classList.remove('show');
  CV.style.display='none';
  if(panelMode) closeDetail();

  if(m==='conf'){
    switchView(currentView);
  } else if(m==='sr'){
    document.getElementById('view-sr').classList.add('show');
    setTimeout(function(){srResize(); buildInsights();},10);
  } else if(m==='proof'){
    document.getElementById('view-proof').classList.add('show');
    renderProof();
  } else if(m==='load'){
    document.getElementById('view-load').classList.add('show');
    renderLoad();
  } else if(m==='origin'){
    document.getElementById('view-origin').classList.add('show');
    renderOrigin();
  } else if(m==='drone'){
    document.getElementById('view-drone').classList.add('show');
    buildDronePage();
  } else if(m==='base'){
    document.getElementById('view-base').classList.add('show');
    buildBasePage();
  } else if(m==='gcp'){
    document.getElementById('view-gcp').classList.add('show');
    buildGcpPage();
  }
  else if(m==='checkpoint'){var _v=document.getElementById('view-checkpoint'); if(_v)_v.classList.add('show'); if(typeof buildCheckpointPage==='function')buildCheckpointPage();}
}

// Hardware popover toggle and outside-click dismiss
function toggleHardwarePopover(e){
  e.stopPropagation();
  var hw=document.getElementById('mod-hw');
  if(!hw) return;
  hw.classList.toggle('open');
}
document.addEventListener('click', function(e){
  var hw=document.getElementById('mod-hw');
  if(!hw || !hw.classList.contains('open')) return;
  // close if click landed outside the Hardware pill
  if(!hw.contains(e.target)) hw.classList.remove('open');
});

// Boot
resize();
// Update master and sentence with OJS from ontology
document.getElementById('ms-num').innerHTML=OJS+'<span style="font-size:.28em;font-weight:700;color:rgba(235,242,248,.38);vertical-align:super;line-height:0;">%</span>';
document.getElementById('sentence-text').innerHTML='Pitpack 4 scored <strong>'+OJS+'%</strong> on the Infinity Loop &mdash; weighted across Capture, Processing, and Analytics universes.';
// Sync global workflow chip in dimbar
(function(){var wf=document.getElementById('dim-wf'); if(wf) wf.innerHTML='<span class="wfk">Workflow</span>'+ONTOLOGY.workflow;})();
buildScoreLabels();
buildDelView();
srInitMap();
buildLayerPanel();
var ldFileInput=document.getElementById('ld-fileinput');
var ldFolderInput=document.getElementById('ld-folderinput');
if(ldFileInput){
  ldFileInput.addEventListener('change',onLoadFileChosen);
  ldFileInput.onchange=onLoadFileChosen;
}
if(ldFolderInput){
  ldFolderInput.addEventListener('change',onLoadFileChosen);
  ldFolderInput.onchange=onLoadFileChosen;
}
// reflect initial Origin state on Load nav badge
(function(){var nbL=document.getElementById('nbadge-load'); if(nbL && !originComplete()) nbL.classList.add('locked');})();
initPulses();
render();

/* ═══════════════════════════════════════════════
   BASE STATION (DATUM hero) logic — namespaced via window.dsBase
   ═══════════════════════════════════════════════ */
(function(){
var TREND=[
  {sid:"S-038",date:"Oct 25",score:84,anom:false},
  {sid:"S-039",date:"Nov 25",score:87,anom:false},
  {sid:"S-040",date:"Nov 25",score:79,anom:true,note:"Antenna height undocumented"},
  {sid:"S-041",date:"Dec 25",score:88,anom:false},
  {sid:"S-042",date:"Dec 25",score:91,anom:false},
  {sid:"S-043",date:"Jan 26",score:88,anom:false},
  {sid:"S-044",date:"Jan 26",score:85,anom:false},
  {sid:"S-045",date:"Feb 26",score:89,anom:false},
  {sid:"S-046",date:"Mar 26",score:92,anom:false},
  {sid:"S-047",date:"May 26",score:87,anom:false}
];
var FLEET=[82,84,80,86,88,86,85,87,89,88];
var fleetOn=false;

/* ============================================================
   CHAIN DATA — ported from base_station_multi_view_v3
   (single source of truth: blocks, 11 indicators, scenarios)
   ============================================================ */
var BLOCKS=[
  {id:"BB_BASE_COMPLETE",name:"Data Completeness & Integrity",weight:0.45,
   description:"Whether the RINEX file fully covers the flight window, recorded without interruption, in supported format, with continuous observations."},
  {id:"BB_BASE_SETUP",name:"Setup & Documentation Confidence",weight:0.35,
   description:"Whether antenna height, mark reference, and equipment metadata are documented and verifiable."},
  {id:"BB_BASE_ENV",name:"Observation Environment Quality",weight:0.20,
   description:"Whether observation conditions (multipath, ionospheric, satellite geometry, acquisition) were favorable."}
];
var GLOBAL_GATE_CONDITION="base_completeness_integrity_score == 0 OR antenna_height_documented_score == 0";

var INDICATOR_LIBRARY={
  "L3I_BASE_001":{num:"#01",block:"BB_BASE_COMPLETE",weight:0.35,name:"Coverage",is_critical_path:true,
    verified_statement:"Base recorded through the entire flight window with adequate pre-flight and post-flight buffer.",
    bands:[
      {score_range:[88,100],level:"good",label:"Full coverage + ≥2 min pre-flight + ≥60s post-flight buffer",impact:null,actions:null},
      {score_range:[72,87],level:"good",label:"Full coverage, pre-flight buffer 60–120s",impact:null,actions:null},
      {score_range:[40,71],level:"review",label:"Full coverage but pre-flight buffer <60s",
        impact:"PPK convergence may be incomplete at flight start. Early position estimates can carry residual error.",
        actions:["Start base recording 2–3 min before takeoff next time","Review processed sigma values on early epochs","Trim any noisy early data if quality is critical"]},
      {score_range:[0,0],level:"resurvey",label:"Base not recording during part of flight (HARD GATE)",
        impact:"PPK cannot correct flight epochs without base coverage. Affected portions have only autonomous-fix accuracy.",
        actions:["Recollect base with adequate flight-window coverage","Start base recording 2–3 min before takeoff","Stop base only after motors off (60s post-landing)"]}
    ],
    derivation:"Threshold 88 reflects industry PPK convergence guidance (2-min pre-flight buffer). 72 reflects convergence possible but compromised. Hard gate at 0 when coverage gap means PPK has no reference for affected epochs."},
  "L3I_BASE_002":{num:"#02",block:"BB_BASE_COMPLETE",weight:0.30,name:"Integrity",is_critical_path:false,
    verified_statement:"Base session ran end-to-end without interruption, log uploaded.",
    bands:[
      {score_range:[100,100],level:"good",label:"Clean session, no interruptions, log uploaded",impact:null,actions:null},
      {score_range:[40,99],level:"review",label:"Operation Log absent — session integrity unconfirmed",
        impact:"Without the operation log, we can't confirm the base shut down cleanly. RINEX may still be usable but the audit trail is incomplete.",
        actions:["Verify RINEX file size matches expected duration","Inspect last epoch timestamp against planned session end","Ensure operation log uploads with base files next time"]},
      {score_range:[0,39],level:"resurvey",label:"Session interrupted (unexpected shutdown)",
        impact:"Base shut down unexpectedly during recording. RINEX may be truncated or corrupted near the shutdown event.",
        actions:["Inspect RINEX around the shutdown timestamp","If shutdown was outside flight window, data may still process","If shutdown was during flight, recollect"]}
    ],
    derivation:"Score 60 (operation log absent) is conservative middle band. Score 20 (interrupted) reflects flight-window shutdowns produce unrecoverable gaps."},
  "L3I_BASE_003":{num:"#03",block:"BB_BASE_COMPLETE",weight:0.20,name:"Format",is_critical_path:false,
    verified_statement:"RINEX is in a supported version with complete header and dual-frequency observations.",
    bands:[
      {score_range:[85,100],level:"good",label:"Supported version, complete header, dual-frequency",impact:null,actions:null},
      {score_range:[40,84],level:"review",label:"Single-frequency or incomplete header",
        impact:"Single-frequency means ionospheric error cannot be modeled — accuracy degrades during active solar weather. Header gaps may need manual patching before processing.",
        actions:["Verify receiver was configured for dual-frequency","Patch missing header fields (antenna, marker) if known","Expect reduced accuracy under high Kp index"]},
      {score_range:[0,39],level:"review",label:"RINEX version not supported by PPK software",
        impact:"Processor cannot ingest this file directly. Data itself is fine — format conversion required, then resubmit.",
        actions:["Convert RINEX to a supported version (3.x or 2.11)","Use vendor converter or standard tool (e.g., teqc, gfzrnx)","Verify converted file passes format validation, then resubmit"]}
    ],
    derivation:"Version-unsupported is review, not resurvey: data is fine, format is fixable via conversion. The operational action is convert+resubmit, not recollect."},
  "L3I_BASE_004":{num:"#04",block:"BB_BASE_COMPLETE",weight:0.15,name:"Continuity",is_critical_path:false,
    verified_statement:"Continuous observations through the session with minimal cycle slips.",
    bands:[
      {score_range:[75,100],level:"good",label:"No gaps or minor only (<60s), minimal cycle slips",impact:null,actions:null},
      {score_range:[0,74],level:"review",label:"Gap >60s detected — PPK must re-converge",
        impact:"Base lost satellite tracking for >60s. PPK must re-converge after the gap, reducing accuracy in that window.",
        actions:["Check whether photos were taken during the gap","Investigate cause (signal blockage, brief power loss)","Position base in clearer location next time"]}
    ],
    derivation:"60s gap threshold from industry PPK best practice. Cycle-slip count below 5% of total epochs is normal."},
  "L3I_BASE_005":{num:"#05",block:"BB_BASE_SETUP",weight:0.55,name:"Antenna height",is_critical_path:true,
    verified_statement:"Antenna height documented to ARP, with field measurement matching RINEX header.",
    bands:[
      {score_range:[85,100],level:"good",label:"Vertical to ARP, matches RINEX",impact:null,actions:null},
      {score_range:[40,84],level:"review",label:"Slant measurement or conflicts with RINEX delta-H",
        impact:"Antenna height measurement carries higher uncertainty. Wrong height shifts all output elevations systematically.",
        actions:["Verify slant-to-vertical conversion was applied","Reconcile form value against RINEX delta-H","Re-measure if uncertain — measure vertical to ARP"]},
      {score_range:[0,0],level:"resurvey",label:"Antenna height not entered (HARD GATE)",
        impact:"PPK output elevations have unknown reference. Without antenna height, output is meaningless regardless of how clean everything else is.",
        actions:["Enter antenna height now if known from field notes","If not recoverable, redo base setup with measured height","Measure 3 times to ARP, average for best confidence"]}
    ],
    derivation:"Antenna height is a true hard gate (forces overall score to 0). Without it, PPK output elevations are uninterpretable regardless of how clean other blocks are."},
  "L3I_BASE_006":{num:"#06",block:"BB_BASE_SETUP",weight:0.30,name:"Setup verification",is_critical_path:false,
    verified_statement:"Base set up over a known reference mark, ideally with second-person verification.",
    bands:[
      {score_range:[75,100],level:"good",label:"Known mark + verification (single or two-person)",impact:null,actions:null},
      {score_range:[0,74],level:"review",label:"Reused mark not re-verified this session",
        impact:"Mark may have shifted since last use. Coordinates carry historical uncertainty.",
        actions:["Re-verify mark via static occupation + CORS","Compare PPK position against published coordinates","Document if verification not feasible"]}
    ],
    derivation:"Score 50 captures unverified-mark concept — a third-state (provenance unverifiable) pending chain-library retrofit."},
  "L3I_BASE_007":{num:"#07",block:"BB_BASE_SETUP",weight:0.15,name:"Antenna type match",is_critical_path:false,
    verified_statement:"Antenna model on form matches RINEX header.",
    bands:[
      {score_range:[85,100],level:"good",label:"Form antenna matches RINEX header",impact:null,actions:null},
      {score_range:[0,84],level:"review",label:"Form antenna differs from RINEX header",
        impact:"Wrong antenna profile means wrong ANTEX calibration — systematic position bias from millimeters to centimeters.",
        actions:["Verify physical antenna against form selection","Update whichever source is incorrect (form or header)","Re-process with corrected ANTEX profile"]}
    ],
    derivation:"Type-string consistency check only — not a true ANTEX phase-center calibration."},
  "L3I_BASE_008":{num:"#08",block:"BB_BASE_ENV",weight:0.45,name:"Multipath",is_critical_path:false,
    verified_statement:"Clean signal environment — low C/N0 variance, no significant multipath risk.",
    bands:[
      {score_range:[75,100],level:"good",label:"Low C/N0 variance — clean signal environment",impact:null,actions:null},
      {score_range:[40,74],level:"review",label:"Moderate C/N0 variance — partial multipath risk",
        impact:"Base was near reflective surfaces. PPK can usually handle it but residuals may show position oscillations.",
        actions:["Check processed residuals for oscillation patterns","Position base ≥10m from buildings/vehicles/water next time","Choose open-sky locations for high-stakes work"]},
      {score_range:[0,39],level:"review",label:"High C/N0 variance — significant multipath risk",
        impact:"Base was clearly in a reflective environment. PPK output likely has position oscillations or elevated residuals.",
        actions:["Inspect PPK position output for wobble","Recollect at cleaner site if accuracy is critical","Scout base location for reflective surfaces in future"]}
    ],
    derivation:"C/N0 variance as multipath proxy is a standard signal-quality metric. Thresholds are heuristic pending calibration against measured PPK residuals."},
  "L3I_BASE_009":{num:"#09",block:"BB_BASE_ENV",weight:0.20,name:"Ionospheric risk",is_critical_path:false,
    verified_statement:"Either calm geomagnetic conditions or dual-frequency observations (ionospheric error modeled).",
    bands:[
      {score_range:[85,100],level:"good",label:"Low Kp OR dual-frequency base",impact:null,actions:null},
      {score_range:[0,84],level:"review",label:"High Kp AND single-frequency only",
        impact:"Active solar weather + single-frequency means ionospheric delay cannot be modeled. Position errors can reach centimeters or more.",
        actions:["Check NOAA SWPC Kp index for survey window","Prefer dual-frequency receiver for next survey","Reschedule if Kp ≥5 and accuracy is critical"]}
    ],
    derivation:"Kp ≥5 threshold from NOAA space weather scale (G1+ geomagnetic storm)."},
  "L3I_BASE_010":{num:"#10",block:"BB_BASE_ENV",weight:0.20,name:"PDOP",is_critical_path:false,
    verified_statement:"Strong satellite geometry — PDOP within standard ranges throughout session.",
    bands:[
      {score_range:[75,100],level:"good",label:"PDOP <4 — good to excellent geometry",impact:null,actions:null},
      {score_range:[0,74],level:"review",label:"PDOP >4 — marginal to poor geometry",
        impact:"Satellite geometry was suboptimal — PPK still works but position uncertainty is elevated.",
        actions:["Check processed sigma values","Use mission planner for future surveys at this site","Reschedule if PDOP forecast >6 and accuracy is critical"]}
    ],
    derivation:"Standard PDOP categorization from GNSS handbooks. 2/4/6 boundaries widely used across industry."},
  "L3I_BASE_011":{num:"#11",block:"BB_BASE_ENV",weight:0.15,name:"Acquisition",is_critical_path:false,
    verified_statement:"Base acquired satellites within normal startup time, no obstruction or interference signs.",
    bands:[
      {score_range:[75,100],level:"good",label:"<3 min — normal startup",impact:null,actions:null},
      {score_range:[0,74],level:"minor",label:">3 min — slow startup (hygiene signal)",
        impact:"Base was slow to lock onto satellites. Usually a cold start. Recurring slowness can indicate receiver health issues, but rarely affects this session's data quality.",
        actions:["Check antenna location for obstructions","Verify firmware version and battery state","Service unit if slow acquisition is recurring"]}
    ],
    derivation:"Acquisition slowness is a minor hygiene signal, not a deliverable-quality concern — hidden from the default view, surfaced only under 'show indicators'."}
};
Object.keys(INDICATOR_LIBRARY).forEach(function(k){INDICATOR_LIBRARY[k].id=k;});
var INDICATORS_ARR=Object.keys(INDICATOR_LIBRARY).map(function(k){return INDICATOR_LIBRARY[k];});

var REAL_SCORES={L3I_BASE_001:100,L3I_BASE_002:100,L3I_BASE_003:88,L3I_BASE_004:82,L3I_BASE_005:85,L3I_BASE_006:70,L3I_BASE_007:80,L3I_BASE_008:78,L3I_BASE_009:100,L3I_BASE_010:85,L3I_BASE_011:70};
var SCENARIOS=[
  {id:"clean",name:"Clean",scores:{L3I_BASE_001:100,L3I_BASE_002:100,L3I_BASE_003:100,L3I_BASE_004:100,L3I_BASE_005:100,L3I_BASE_006:100,L3I_BASE_007:100,L3I_BASE_008:100,L3I_BASE_009:100,L3I_BASE_010:100,L3I_BASE_011:100}},
  {id:"review",name:"Review",scores:{L3I_BASE_001:100,L3I_BASE_002:100,L3I_BASE_003:100,L3I_BASE_004:100,L3I_BASE_005:55,L3I_BASE_006:100,L3I_BASE_007:40,L3I_BASE_008:65,L3I_BASE_009:100,L3I_BASE_010:100,L3I_BASE_011:100}},
  {id:"resurvey",name:"Resurvey",scores:{L3I_BASE_001:0,L3I_BASE_002:100,L3I_BASE_003:100,L3I_BASE_004:100,L3I_BASE_005:100,L3I_BASE_006:100,L3I_BASE_007:100,L3I_BASE_008:100,L3I_BASE_009:100,L3I_BASE_010:100,L3I_BASE_011:100}}
];

/* ---- scoring helpers (from v3) ---- */
function getBandForScore(ind,score){
  for(var i=0;i<ind.bands.length;i++){var b=ind.bands[i];if(score>=b.score_range[0]&&score<=b.score_range[1])return b;}
  return ind.bands[ind.bands.length-1];
}
function severityForBand(b){
  if(b.level==="resurvey")return"critical";
  if(b.level==="review")return"material";
  if(b.level==="minor")return"minor";
  return"none";
}
function blockIndicators(blockId){return INDICATORS_ARR.filter(function(i){return i.block===blockId;});}
function computeBlockScore(blockId,scores){
  var inds=blockIndicators(blockId),tw=0,sw=0;
  inds.forEach(function(i){var s=scores[i.id];if(s===undefined)return;tw+=i.weight;sw+=i.weight*s;});
  return tw>0?sw/tw:0;
}
function checkHardGate(scores){
  for(var i=0;i<INDICATORS_ARR.length;i++){var ind=INDICATORS_ARR[i];if(ind.is_critical_path&&scores[ind.id]===0)return{fired:true,source:ind};}
  return{fired:false,source:null};
}
function computeOverallScore(scores){
  var gate=checkHardGate(scores);
  if(gate.fired)return{score:0,hardGate:true,gateSource:gate.source};
  var tw=0,sw=0;
  BLOCKS.forEach(function(b){var bs=computeBlockScore(b.id,scores);tw+=b.weight;sw+=b.weight*bs;});
  return{score:tw>0?sw/tw:0,hardGate:false};
}
function overallRecommendation(scores){
  var overall=computeOverallScore(scores);
  if(overall.hardGate)return{rec:"resurvey",overall:overall};
  for(var i=0;i<INDICATORS_ARR.length;i++){if(severityForBand(getBandForScore(INDICATORS_ARR[i],scores[INDICATORS_ARR[i].id]))==="critical")return{rec:"resurvey",overall:overall};}
  for(var j=0;j<INDICATORS_ARR.length;j++){if(severityForBand(getBandForScore(INDICATORS_ARR[j],scores[INDICATORS_ARR[j].id]))==="material")return{rec:"review",overall:overall};}
  return{rec:"good",overall:overall};
}
function blockLevel(blockId,scores){
  var inds=blockIndicators(blockId),worst="good";
  inds.forEach(function(i){
    var lvl=getBandForScore(i,scores[i.id]).level;
    if(lvl==="resurvey")worst="resurvey";
    else if(lvl==="review"&&worst!=="resurvey")worst="review";
  });
  return worst;
}
var REC_REASON={
  good:"All checks passed. Base data is clean and survey-grade — clear to proceed.",
  review:"Setup &amp; documentation needs a quick check before you proceed — see the flagged blocks below.",
  resurvey:"A hard gate fired. This base session can't produce usable PPK output as-is and needs to be recollected."
};
var REC_LABEL={good:"GOOD TO GO",review:"REVIEW",resurvey:"RESURVEY"};
var REC_VERDICT_COLOR={good:"rgba(16,185,214,.9)",review:"rgba(232,228,218,.94)",resurvey:"var(--red)"};



/* sparkline */
(function(){
  var svg=document.getElementById("sparkSvg");
  var W=192,H=52,pL=2,pR=2,pT=5,pB=5;
  var n=TREND.length,mn=75,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*(W-pL-pR):(W-pL-pR)/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*(H-pT-pB)};
  var area="M "+pL+" "+(H-pB);
  TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});
  area+=" L "+(W-pR)+" "+(H-pB)+" Z";
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  var s='<path fill="url(#spGrad)" d="'+area+'"/>';
  s+='<path fill="none" stroke="rgba(16,185,214,.5)" stroke-width="1" d="'+line+'"/>';
  TREND.forEach(function(d,i){
    var x=sx(i),y=sy(d.score);
    s+='<circle fill="'+(d.anom?"rgba(232,228,218,.4)":"rgba(16,185,214,.5)")+'" cx="'+x+'" cy="'+y+'" r="1.8"><title>'+d.sid+" · "+d.score+"</title></circle>";
  });
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1" cx="'+lx+'" cy="'+ly+'" r="3.8"/>';
  svg.innerHTML+=s;
})();

/* trend modal */
function openTrend(){document.getElementById("trendModal").classList.add("open");drawTrend()}
function closeTrend(){document.getElementById("trendModal").classList.remove("open")}
function toggleFleet(){fleetOn=!fleetOn;document.getElementById("fleetBtn").classList.toggle("on",fleetOn);drawTrend()}
function drawTrend(){
  var svg=document.getElementById("trendSvg");
  var W=900,H=256,pL=44,pR=24,pT=14,pB=34;
  var iW=W-pL-pR,iH=H-pT-pB;
  var n=TREND.length,mn=40,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*iW:iW/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*iH};
  var s='<defs><linearGradient id="tgGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(16,185,214,.25)"/><stop offset="100%" stop-color="rgba(16,185,214,.00)"/></linearGradient></defs>';
  [40,60,75,90,100].forEach(function(v){
    s+='<line class="tg-axis" x1="'+pL+'" y1="'+sy(v)+'" x2="'+(W-pR)+'" y2="'+sy(v)+'"/>';
    s+='<text class="tg-tick" x="'+(pL-6)+'" y="'+(sy(v)+3)+'" text-anchor="end">'+v+'</text>';
  });
  s+='<rect class="tg-band" x="'+pL+'" y="'+sy(100)+'" width="'+iW+'" height="'+(sy(90)-sy(100))+'"/>';
  s+='<text x="'+(W-pR+4)+'" y="'+(sy(90)+3)+'" font-family="IBM Plex Mono" font-size="8" fill="rgba(16,185,214,.28)">Survey</text>';
  s+='<text x="'+(W-pR+4)+'" y="'+(sy(75)+3)+'" font-family="IBM Plex Mono" font-size="8" fill="rgba(200,210,220,.16)">Eng.</text>';
  s+='<line stroke="rgba(232,228,218,.12)" stroke-width=".5" stroke-dasharray="3 3" x1="'+pL+'" y1="'+sy(87)+'" x2="'+(W-pR)+'" y2="'+sy(87)+'"/>';
  var area="M "+pL+" "+sy(mn);
  TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});
  area+=" L "+(W-pR)+" "+sy(mn)+" Z";
  s+='<path class="tg-area" d="'+area+'"/>';
  if(fleetOn){
    var fp="";FLEET.forEach(function(v,i){fp+=(i===0?"M ":"L ")+sx(i)+" "+sy(v)+" "});
    s+='<path class="tg-fleet" d="'+fp+'"/>';
    s+='<text class="tg-lbl" x="'+(sx(FLEET.length-1)+5)+'" y="'+(sy(FLEET[FLEET.length-1])+3)+'">fleet median</text>';
  }
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  s+='<path class="tg-line" d="'+line+'"/>';
  TREND.forEach(function(d,i){
    var x=sx(i),y=sy(d.score);
    s+='<circle class="tg-pt'+(d.anom?" anom":"")+'" cx="'+x+'" cy="'+y+'" r="4.5"><title>'+d.sid+" · "+d.score+(d.note?" ("+d.note+")":"")+"</title></circle>";
    if(i%2===0||i===n-1)s+='<text class="tg-tick" x="'+x+'" y="'+(H-pB+13)+'" text-anchor="middle">'+d.date+'</text>';
  });
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1"/>';
  s+='<text class="tg-lbl" x="'+(lx-7)+'" y="'+(ly-11)+'" text-anchor="end" fill="rgba(16,185,214,.6)">current · '+TREND[n-1].score+'</text>';
  svg.innerHTML=s;
}

/* bb section toggle */
function toggleBBSection(){
  var body=document.getElementById("bbSectionBody");
  var icon=document.getElementById("bbSectionIcon");
  body.classList.toggle("open");
  icon.classList.toggle("open");
}

/* right panel cats */
function toggleCat(id){document.getElementById(id).classList.toggle("open")}


/* ============================================================
   RENDER LAYER — drives UI from chain data + active scenario
   ============================================================ */
SCENARIOS.unshift({id:'actual',name:'Actual',scores:REAL_SCORES});
var currentScenario=SCENARIOS[0]; // default: Review
var selected={};   // blockId -> bool (show indicators)

/* pill positions around the DATUM render, by indicator slot count */
var POS={
  1:[[50,16]],
  2:[[26,30],[74,30]],
  3:[[24,40],[76,40],[50,84]],
  4:[[22,32],[78,32],[28,76],[72,76]]
};

function pctRound(n){return Math.round(n);}

function renderScenarioPicker(){
  var el=document.getElementById("scnPick");
  el.innerHTML=SCENARIOS.map(function(s){
    var on=s.id===currentScenario.id;
    var cls="scn-opt"+(on?" on":"");
    if(on&&s.id==="review")cls+=" warn";
    if(on&&s.id==="resurvey")cls+=" bad";
    return '<button class="'+cls+'" onclick="dsBase.selectScenario(\''+s.id+'\')">'+s.name+'</button>';
  }).join("");
}

function selectScenario(id){
  var s=SCENARIOS.filter(function(x){return x.id===id;})[0];
  if(!s)return;
  currentScenario=s;
  selected={};                 // reset pill toggles on scenario change
  closeDrawer();
  renderAll();
}

function statusText(level){
  return level==="good"?"OK":(level==="resurvey"?"Resurvey":"Review");
}

function renderHeadline(){
  var scores=currentScenario.scores;
  var rec=overallRecommendation(scores);
  var overall=pctRound(rec.overall.score);
  document.getElementById("scoreNum").innerHTML=overall+'<span class="pct">%</span>';
  document.getElementById("scoreDelta").textContent=
    rec.rec==="resurvey"?"Hard gate — score forced to 0":"Weighted across 3 blocks";
  // recommendation card
  var vt=document.getElementById("mdVerdictText");
  vt.textContent=REC_LABEL[rec.rec];
  var verdict=document.getElementById("mdVerdict");
  verdict.style.color=REC_VERDICT_COLOR[rec.rec];
  document.getElementById("mdReason").innerHTML=REC_REASON[rec.rec];
}

function renderBBCards(){
  var scores=currentScenario.scores;
  var host=document.getElementById("bbStripHead");
  host.innerHTML=BLOCKS.map(function(b,idx){
    var bs=pctRound(computeBlockScore(b.id,scores));
    var lvl=blockLevel(b.id,scores);
    var cls="bb-card"+(lvl==="review"?" review":"")+(lvl==="resurvey"?" resurvey":"");
    var num="BB · 0"+(idx+1);
    var fillW=lvl==="good"?100:bs;
    var fillCol=lvl==="good"?"rgba(16,185,214,.38)":(lvl==="resurvey"?"rgba(201,64,64,.5)":"rgba(232,228,218,.18)");
    return ''+
      '<div class="'+cls+'" id="'+b.id+'">'+
        '<div class="bb-header"><div class="bb-h-left">'+
          '<div class="bb-num">'+num+'</div>'+
          '<div class="bb-name">'+b.name+'</div>'+
          '<div class="bb-weight">weight '+b.weight.toFixed(2)+'</div>'+
        '</div><div class="bb-h-right">'+
          '<div class="bb-score-sm">'+bs+'%</div>'+
          '<div class="bb-status-dot"></div>'+
        '</div></div>'+
        '<div class="bb-inner-always">'+
          '<div class="bb-bar"><div class="bb-bar-fill" style="width:'+fillW+'%;background:'+fillCol+'"></div></div>'+
          '<div class="bb-toggle-row" onclick="dsBase.toggleBBIndicators(\''+b.id+'\')"><span class="bb-check"></span><span class="bb-toggle-text">Show indicators</span></div>'+
          '<div class="bb-status-full">'+statusText(lvl)+'</div>'+
          '<div class="bb-details" onclick="event.stopPropagation();dsBase.openBBDetails(\''+b.id+'\')">Details ›</div>'+
        '</div>'+
      '</div>';
  }).join("");
  markActiveBB();
}

function renderIndicators(){
  var layer=document.getElementById("indicatorLayer");
  if(!layer)return;
  var scores=currentScenario.scores;
  var html=[];
  BLOCKS.forEach(function(b){
    if(!selected[b.id])return;
    var inds=blockIndicators(b.id);
    var pts=POS[inds.length]||POS[4];
    inds.forEach(function(ind,i){
      var p=pts[i]||[50,75];
      var lvl=getBandForScore(ind,scores[ind.id]).level;
      var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
      html.push('<div class="indicator-pill'+sev+'" style="left:'+p[0]+'%;top:'+p[1]+'%"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+scores[ind.id]+'</b></div>');
    });
  });
  layer.innerHTML=html.join("");
  layer.className="indicator-layer"+(html.length?" show":"");
}

function toggleBBIndicators(id){selected[id]=!selected[id];markActiveBB();renderIndicators();}
function markActiveBB(){
  BLOCKS.forEach(function(b){var el=document.getElementById(b.id);if(el)el.classList.toggle("active",!!selected[b.id]);});
}

/* drawer: per-block indicator decomposition with band content */
function openBBDetails(blockId){
  selected[blockId]=true;markActiveBB();renderIndicators();
  var b=BLOCKS.filter(function(x){return x.id===blockId;})[0];
  var scores=currentScenario.scores;
  var bs=pctRound(computeBlockScore(blockId,scores));
  var lvl=blockLevel(blockId,scores);
  var inds=blockIndicators(blockId);
  var rows=inds.map(function(ind){
    var sc=scores[ind.id];
    var band=getBandForScore(ind,sc);
    var sevCls=band.level==="good"?"":(band.level==="resurvey"?"resurvey":(band.level==="minor"?"":"review"));
    var html='<div class="d-ind"><div class="d-ind-top">'+
      '<div class="d-ind-name">'+ind.num+'  '+ind.name+'</div>'+
      '<div class="d-ind-sc '+sevCls+'">'+sc+'</div></div>'+
      '<div class="d-ind-band">'+(band.level==="good"?ind.verified_statement:band.label)+'</div>';
    if(band.impact)html+='<div class="d-ind-impact">'+band.impact+'</div>';
    if(band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+a+'</li>';}).join("")+'</ul>';
    html+='<div class="d-deriv">'+ind.derivation+'</div></div>';
    return html;
  }).join("");
  document.getElementById("drawerBody").innerHTML=
    '<h2>'+b.name+'</h2>'+
    '<div class="d-score">'+bs+'<span>%</span></div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
    '<div class="d-narr">'+b.description+'</div>'+
    '<div class="d-sec">Indicators</div>'+
    rows;
  openDrawer();
}

/* recommendation drawer: Actionables + Verified, both collapsible */
function importance(ind){
  var bw=BLOCKS.filter(function(b){return b.id===ind.block;})[0].weight;
  return (ind.is_critical_path?1000:0)+bw*ind.weight*100;
}
function accPanel(ind,scores,kind,open){
  // kind: 'verified' | 'review' | 'resurvey' | 'noted'
  var band=getBandForScore(ind,scores[ind.id]);
  var scCls=kind==="resurvey"?"resurvey":(kind==="review"?"review":"");
  var tagTxt={verified:"Verified",review:"Review",resurvey:"Resurvey",noted:"Noted"}[kind];
  var body;
  if(kind==="verified"){
    body='<div class="acc-state">'+ind.verified_statement+'</div>'+
         '<div class="acc-evi">Evidence · '+band.label+'</div>'+fmtLiveInputs(ind);
  }else if(kind==="noted"){
    body='<div class="acc-state">'+band.label+'</div>'+
         (band.impact?'<div class="d-ind-impact">'+band.impact+'</div>':'')+
         (band.actions?'<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+a+'</li>';}).join("")+'</ul>':'');
  }else{
    body='<div class="acc-state">'+band.label+'</div>'+
         (band.impact?'<div class="d-ind-impact">'+band.impact+'</div>':'')+
         (band.actions?'<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+a+'</li>';}).join("")+'</ul>':'');
  }
  return '<div class="acc'+(open?" open":"")+'">'+
      '<div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
        '<span class="acc-chev">▶</span>'+
        '<span class="acc-name">'+ind.name+'</span>'+
        '<span class="acc-right"><span class="acc-sc '+scCls+'">'+scores[ind.id]+'</span>'+
          '</span>'+
      '</div>'+
      '<div class="acc-body"><div class="acc-inner">'+body+'</div></div>'+
    '</div>';
}
function setSection(sel,open){
  var rows=document.querySelectorAll(sel+" .acc");
  for(var i=0;i<rows.length;i++)rows[i].classList.toggle("open",open);
}
function verifiedBlock(count,listHtml){var head='<div class="d-sec-row"><div class="d-sec verified">Verified<span class="d-sec-count">'+count+'</span></div></div>';if(count<=0) return head+'<div id="verSec">'+listHtml+'</div>';var summary=(typeof INDICATORS_ARR!=='undefined'&&count===INDICATORS_ARR.length)?('All '+count+' indicators passed verification.'):(count+' indicators verified and in good standing.');return '<div class="d-sec-row"><div style="display:flex;align-items:baseline;gap:10px;flex:1;min-width:0"><span class="d-sec verified" style="margin:0;padding:0;border:0;flex-shrink:0">Verified</span><span class="d-empty" style="padding:0">'+summary+'</span></div>'+'<button class="d-ctrl" id="verToggle" onclick="dsBase.toggleVerified()" style="flex-shrink:0">+ More Details</button></div>'+'<div id="verSec" style="display:none">'+listHtml+'</div>';}
function toggleVerified(){var sec=document.getElementById('verSec'),tog=document.getElementById('verToggle');if(!sec||!tog)return;var open=(sec.style.display==='none');sec.style.display=open?'block':'none';tog.innerHTML=open?'\u2212 Show less':'+ More Details';}
function openRecommendation(){
  var scores=currentScenario.scores;
  var rec=overallRecommendation(scores);
  var overall=pctRound(rec.overall.score);
  var actionable=[],verified=[],noted=[];
  INDICATORS_ARR.forEach(function(ind){
    var sev=severityForBand(getBandForScore(ind,scores[ind.id]));
    if(sev==="critical")actionable.push({ind:ind,kind:"resurvey",rank:0});
    else if(sev==="material")actionable.push({ind:ind,kind:"review",rank:1});
    else if(sev==="minor")noted.push({ind:ind,kind:"noted"});
    else verified.push({ind:ind,kind:"verified"});
  });
  actionable.sort(function(a,b){return a.rank-b.rank||importance(b.ind)-importance(a.ind);});
  verified.sort(function(a,b){return importance(b.ind)-importance(a.ind);});

  var gateHtml=rec.overall.hardGate?
    '<div class="d-gate">HARD GATE — '+rec.overall.gateSource.name+' scored 0, forcing overall to 0. Gate: '+GLOBAL_GATE_CONDITION+'</div>':'';

  // Actionables expanded by default; Verified expanded only when nothing to action.
  var verifiedOpen=actionable.length===0;
  var actHtml=actionable.length
    ? actionable.map(function(f){return accPanel(f.ind,scores,f.kind,true);}).join("")
    : '<div class="d-empty">Nothing to action — no Review or Resurvey findings.</div>';
  var notedHtml=noted.map(function(f){return accPanel(f.ind,scores,"noted",false);}).join("");
  var verHtml=verified.length
    ? verified.map(function(f){return accPanel(f.ind,scores,"verified",false);}).join("")
    : '<div class="d-empty">No checks passed cleanly.</div>';

  document.getElementById("drawerBody").innerHTML=
    '<h2>Why '+REC_LABEL[rec.rec].replace("GOOD TO GO","Good to go")+'?</h2>'+
    
    
    '<div class="d-narr">'+REC_REASON[rec.rec]+'</div>'+
    gateHtml+
    '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+actionable.length+'</span></div>'+
      '<div class="d-ctrls"><button class="d-ctrl" onclick="dsBase.setSection(\'#actSec\',true)">Expand all</button>'+
      '<button class="d-ctrl" onclick="dsBase.setSection(\'#actSec\',false)">Collapse all</button></div></div>'+
    '<div id="actSec">'+actHtml+notedHtml+'</div>'+
    verifiedBlock(verified.length, verHtml);
  openDrawer();
}


function openDrawer(){document.getElementById("drawer").classList.add("open");}
function closeDrawer(){document.getElementById("drawer").classList.remove("open");}
document.addEventListener("keydown",function(e){if(e.key==="Escape"){closeTrend();closeDrawer();}});

var BASE_API_READY=false;
function renderBaseNoApi(msg){
  var score=document.getElementById("scoreNum"); if(score) score.innerHTML='<span style="font-size:28px;opacity:.45;letter-spacing:.1em">NO API DATA</span>';
  var delta=document.getElementById("scoreDelta"); if(delta) delta.textContent=msg||"Start the API and refresh the database.";
  var reason=document.getElementById("mdReason"); if(reason) reason.textContent=msg||"No Base Station API data loaded.";
  var pick=document.getElementById("scnPick"); if(pick) pick.innerHTML="";
  var cards=document.getElementById("bbStripHead"); if(cards) cards.innerHTML='<div class="d-empty">No Base Station records returned by the API.</div>';
  var layer=document.getElementById("indicatorLayer"); if(layer){layer.innerHTML="";layer.className="indicator-layer";}
}
function renderAll(){
  if(!BASE_API_READY){renderBaseNoApi();return;}
  renderScenarioPicker();
  renderHeadline();
  renderBBCards();
  renderIndicators();
}

var REAL_OVERALL=computeOverallScore(REAL_SCORES).score;
window.dsBase={openTrend:openTrend,closeTrend:closeTrend,toggleFleet:toggleFleet,toggleBBSection:toggleBBSection,selectScenario:selectScenario,toggleBBIndicators:toggleBBIndicators,openBBDetails:openBBDetails,openRecommendation:openRecommendation,closeDrawer:closeDrawer,setSection:setSection,toggleVerified:toggleVerified,render:renderAll,refreshApi:function(){ if(!BASE_API_READY) loadLiveScores(); },realScore:REAL_OVERALL};

/* ============================================================
   INDICATOR CALCULATION ENGINE  (ported from v13_3 — chain v2.1 LOCKED)
   Mirrors the Python chain logic. Accepts raw input_values from
   /api/indicators and returns {L3I_BASE_001: score, ...}
   ============================================================ */

function calcL3I_BASE_001(v) {
  // coverage_score: coverage_ratio + pre/post buffers
  if (!v || v.coverage_ratio < 1.0) return 0;          // HARD GATE
  if (v.pre_buffer_sec >= 120) return 100;              // full coverage + ≥2 min pre
  if (v.pre_buffer_sec >= 60)  return 80;               // full coverage, 60–120s pre
  if (v.pre_buffer_sec >= 0)   return 55;               // full coverage, pre <60s
  return 0;
}

function calcL3I_BASE_002(v) {
  // integrity_score: shutdown count + log presence
  if (!v) return 60;
  if (v.session_completed_normally === false ||
      (v.unexpected_shutdown_count != null && v.unexpected_shutdown_count > 0)) return 20;
  if (v.raw_log_download_confirmed === false) return 60;
  return 100;
}

function calcL3I_BASE_003(v) {
  // format_score: rinex_version + header_complete + dual_freq
  if (!v) return 60;
  var supported = v.rinex_version_supported !== false;
  if (!supported) return 20;                               // unsupported version
  if (v.dual_freq_available && v.header_complete) return 100;
  if (v.dual_freq_available || v.header_complete) return 60;  // partial
  return 40;                                               // single-freq / incomplete header
}

function calcL3I_BASE_004(v) {
  // continuity_score: gaps + cycle slips
  if (!v) return 75;
  if (v.any_gap_gt_60s) return 50;
  if (v.cycle_slips_per_hour != null && v.cycle_slips_per_hour >= 100) return 60;
  return 100;
}

function calcL3I_BASE_005(v) {
  // antenna_height_documented_score — HARD GATE if missing
  if (!v) return 0;
  if (v.antenna_height_m == null || v.antenna_height_m === 0) return 0;  // HARD GATE
  var vertOk    = (v.antenna_measurement_type === "VERTICAL");
  var arpOk     = (v.measured_to_reference === "ARP");
  var countOk   = (v.height_measured_count != null && v.height_measured_count >= 3);
  var agreeOk   = (v.antenna_height_agreement == null || v.antenna_height_agreement === true);
  if (vertOk && arpOk && countOk && agreeOk) return 100;
  if (vertOk && arpOk) return 85;
  return 55;  // slant or mismatch
}

function calcL3I_BASE_006(v) {
  // setup_verification_score
  if (!v) return 50;
  if (!v.over_known_mark) return 50;
  if (v.verified_by_second_person) return 100;
  return 75;  // known mark but single-person only
}

function calcL3I_BASE_007(v) {
  // antenna_type_match_score
  if (!v) return 50;
  return v.antenna_type_match ? 100 : 40;
}

function calcL3I_BASE_008(v) {
  // multipath_score: mean_of_per_sat_cn0_std_dbhz
  if (!v || v.mean_of_per_sat_cn0_std_dbhz == null) return 75;
  var std = v.mean_of_per_sat_cn0_std_dbhz;
  var lo  = (v.thresholds_dbhz && v.thresholds_dbhz.low)  || 2.5;
  var hi  = (v.thresholds_dbhz && v.thresholds_dbhz.high) || 4.0;
  if (std < lo)  return 100;
  if (std < hi)  return 60;
  return 20;
}

function calcL3I_BASE_009(v) {
  // ionospheric_risk_score: kp_index + dual_freq fallback
  if (!v) return 100;
  if (v.dual_freq_available) return 100;       // dual-freq always mitigates iono
  if (v.kp_status === "API_UNAVAILABLE") return 100; // can't penalise without data
  var kpThresh = v.kp_high_threshold || 5.0;
  if (v.kp_index != null && v.kp_index >= kpThresh) return 40;
  return 100;
}

function calcL3I_BASE_010(v) {
  // pdop_score: mean_pdop
  if (!v || v.mean_pdop == null) return 100;
  if (v.mean_pdop < 2)  return 100;
  if (v.mean_pdop < 4)  return 85;
  if (v.mean_pdop < 6)  return 50;
  return 20;
}

function calcL3I_BASE_011(v) {
  // acquisition_score: base_acquisition_time_sec
  if (!v || v.base_acquisition_time_sec == null) return 100;
  return v.base_acquisition_time_sec < 180 ? 100 : 50;
}

/* Master function: takes the full indicators array from /api/indicators
   and returns a scores map {L3I_BASE_001: n, ...} calculated from input_values */
function calculateScoresFromInputs(indicators) {
  var calcs = {
    L3I_BASE_001: calcL3I_BASE_001,
    L3I_BASE_002: calcL3I_BASE_002,
    L3I_BASE_003: calcL3I_BASE_003,
    L3I_BASE_004: calcL3I_BASE_004,
    L3I_BASE_005: calcL3I_BASE_005,
    L3I_BASE_006: calcL3I_BASE_006,
    L3I_BASE_007: calcL3I_BASE_007,
    L3I_BASE_008: calcL3I_BASE_008,
    L3I_BASE_009: calcL3I_BASE_009,
    L3I_BASE_010: calcL3I_BASE_010,
    L3I_BASE_011: calcL3I_BASE_011
  };
  var scores = {};
  indicators.forEach(function(ind) {
    var fn = calcs[ind.id];
    scores[ind.id] = fn ? fn(ind.input_values || {}) : (ind.score || 0);
  });
  return scores;
}

/* Comparison helper: returns per-indicator diff between API score and calculated score.
   Surfaces in the drawer as a small badge when they diverge > 2 pts. */
function scoreDiff(apiScore, calcScore) {
  return Math.round(apiScore) - Math.round(calcScore);
}

/* ---- live input_values formatter ---- */
function fmtLiveInputs(ind) {
  if (!ind._liveInputs || !currentScenario._live) return "";
  var pairs = Object.entries
    ? Object.entries(ind._liveInputs)
    : Object.keys(ind._liveInputs).map(function(k){ return [k, ind._liveInputs[k]]; });
  if (!pairs.length) return "";
  var items = pairs.map(function(kv) {
    var k = kv[0].replace(/_/g," ");
    var v = kv[1];
    if (v === null || v === undefined) v = "—";
    else if (typeof v === "boolean") v = v ? "yes" : "no";
    else if (typeof v === "object") v = JSON.stringify(v);
    return '<span class="live-kv"><span class="live-k">'+k+'</span><span class="live-v">'+v+'</span></span>';
  }).join("");
  var diff = (typeof ind._apiScore !== "undefined" && typeof ind._calcScore !== "undefined")
    ? scoreDiff(ind._calcScore, ind._apiScore) : null;   /* calc minus API */
  var diffBadge = "";
  if (diff !== null && Math.abs(diff) > 2) {
    var sign = diff > 0 ? "+" : "";
    diffBadge = '<div class="live-diff">API score: ' + ind._apiScore +
      '  ·  calculated: ' + ind._calcScore +
      '  ·  diff: <span class="live-diff-val ' + (diff !== 0 ? "mismatch":"") + '">' +
      sign + diff + '</span></div>';
  }
  return '<div class="live-inputs">'+items+'</div>' + diffBadge;
}

/* ============================================================
   LIVE DATA — fetch from /api/indicators and hydrate UI
   ============================================================ */

var API_URL = loopApiUrl("/api/base-station/indicators");
var BASE_API_RETRY_COUNT=0;
var BASE_API_RETRY_MAX=240;
var BASE_API_RETRY_MS=3000;
var BASE_API_LOADING=false;

function mapApiToScores(indicators) {
  /* Build the scores map {L3I_BASE_001: 100, ...} from API response */
  var scores = {};
  indicators.forEach(function(ind) {
    var id = ind.id || ind.indicator_id;
    if(id) scores[id] = ind.score;
  });
  return scores;
}

function showLoadingState() {
  var el = document.getElementById("scoreNum");
  if (el) el.innerHTML = '<span style="font-size:28px;opacity:.4;letter-spacing:.1em">LOADING</span>';
}

function showErrorBadge(msg) {
  /* Surface a small non-blocking badge so the dev sees the error */
  var badge = document.createElement("div");
  badge.style.cssText = [
    "position:fixed;bottom:18px;left:50%;transform:translateX(-50%)",
    "background:rgba(201,64,64,.18);border:1px solid rgba(201,64,64,.4)",
    "color:rgba(232,228,218,.7);font-family:var(--fm);font-size:10px",
    "letter-spacing:.12em;padding:6px 14px;border-radius:2px;z-index:9999",
    "pointer-events:none"
  ].join(";");
  badge.textContent = "API UNAVAILABLE - no live data loaded  ·  " + msg;
  document.body.appendChild(badge);
  setTimeout(function(){ badge.remove(); }, 6000);
}

function injectLiveScenario(indicators) {
  /* Calculate scores from raw inputs using the chain engine */
  var calcedScores  = calculateScoresFromInputs(indicators);
  var apiScores     = mapApiToScores(indicators);

  /* Use API scores as the authoritative display values; calcedScores are kept for diff comparison only */
  var scores = apiScores;

  /* Store both on INDICATOR_LIBRARY for the drawer diff badge */
  indicators.forEach(function(ind) {
    ind.id = ind.id || ind.indicator_id;
    var lib = INDICATOR_LIBRARY[ind.id];
    if (!lib) return;
    lib._apiScore  = apiScores[ind.id];
    lib._calcScore = calcedScores[ind.id];
  });

  /* Store input_values + band_matched + condition onto INDICATOR_LIBRARY entries
     so the drawer (openBBDetails / openRecommendation) can surface real field data */
  indicators.forEach(function(ind) {
    var lib = INDICATOR_LIBRARY[ind.id];
    if (!lib) return;
    lib._liveInputs    = ind.input_values  || {};
    lib._liveBand      = ind.band_matched  || null;
    lib._liveCondition = ind.condition     || null;
  });

  var liveScenario = {
    id:     "live",
    name:   "Live",
    scores: scores,
    _live:  true   /* sentinel so picker can style it */
  };

  SCENARIOS.splice(0, SCENARIOS.length, liveScenario);
  currentScenario = liveScenario;
  BASE_API_READY = true;
  if(window.dsBase){
    window.dsBase.realScore = computeOverallScore(scores).score;
  }
}

function loadLiveScores() {
  if(BASE_API_LOADING) return;
  BASE_API_LOADING=true;
  showLoadingState();

  fetch(withCacheBust(API_URL),{cache:'no-store'})
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      BASE_API_LOADING=false;
      var indicators = data.indicators || data; /* handle both {indicators:[]} and bare [] */
      if (!Array.isArray(indicators) || indicators.length === 0) {
        throw new Error("empty indicators array");
      }
      BASE_API_RETRY_COUNT=0;
      injectLiveScenario(indicators);
      renderAll();
    })
    .catch(function(err) {
      /* Graceful fallback — keep currentScenario as-is */
      BASE_API_LOADING=false;
      if(BASE_API_RETRY_COUNT===0 || BASE_API_RETRY_COUNT%20===0) showErrorBadge(err.message || String(err));
      BASE_API_READY = false;
      renderBaseNoApi(err.message || String(err));
      if(BASE_API_RETRY_COUNT<BASE_API_RETRY_MAX){
        BASE_API_RETRY_COUNT++;
        setTimeout(loadLiveScores,BASE_API_RETRY_MS);
      }
    });
}

window.loadLiveScores = loadLiveScores;
loadLiveScores();

})();

/* ── BASE → GLOBAL CONFIDENCE wiring (single real state only) ── */
(function(){
  var real = (window.dsBase && typeof window.dsBase.realScore==='number')
             ? Math.round(window.dsBase.realScore) : 87;
  if(typeof SUB_CAPTURE_BASE!=='undefined') SUB_CAPTURE_BASE.score = real;
  try{ BASE_OVERALL_SCORE = real; }catch(e){}
  // Capture universe is now DERIVED from its subsystem real scores, so Base flows up.
  // Documented intra-Capture subsystem weights:
  var W={drone:0.35, base:0.30, gcp:0.20, preproc:0.15};
  var sc={
    drone:(typeof SUB_CAPTURE_DRONE!=='undefined')?SUB_CAPTURE_DRONE.score:94,
    base:real,
    gcp:(typeof SUB_CAPTURE_GCP!=='undefined')?SUB_CAPTURE_GCP.score:78,
    preproc:(typeof SUB_CAPTURE_PREPROC!=='undefined')?SUB_CAPTURE_PREPROC.score:90
  };
  var capScore=Math.round(W.drone*sc.drone+W.base*sc.base+W.gcp*sc.gcp+W.preproc*sc.preproc);
  if(typeof ONTOLOGY!=='undefined' && ONTOLOGY.universes && ONTOLOGY.universes[0]){
    ONTOLOGY.universes[0].score=capScore;
    if(typeof GATES!=='undefined' && GATES[0]){
      GATES[0].score=capScore;
      if(GATES[0].universe) GATES[0].universe.score=capScore;
    }
    var nOJS=Math.round(
      ONTOLOGY.universes[0].score*ONTOLOGY.universes[0].weight +
      ONTOLOGY.universes[1].score*ONTOLOGY.universes[1].weight +
      ONTOLOGY.universes[2].score*ONTOLOGY.universes[2].weight);
    var ms=document.getElementById('ms-num');
    if(ms) ms.innerHTML=nOJS+'<span style="font-size:.28em;font-weight:700;color:rgba(235,242,248,.38);vertical-align:super;line-height:0;">%</span>';
    var st=document.getElementById('sentence-text');
    if(st) st.innerHTML='Pitpack 4 scored <strong>'+nOJS+'%</strong> on the Infinity Loop &mdash; up 2.3% from last survey, trending toward Professional Grade across 11 missions.';
    if(typeof buildScoreLabels==='function'){try{buildScoreLabels();}catch(e){}}
  }
})();
/* route the Hardware→Base entry to the new DATUM renderer */
buildBasePage = function(){
  if(window.dsBase){
    if(window.dsBase.refreshApi) window.dsBase.refreshApi();
    window.dsBase.render();
  }
};


/* ═══════════════════════════════════════════════
   DRONE (DATUM hero) — locked chain + UI, namespaced via window.dsDrone
   Data+engine ported verbatim from drone_multi_view_v1_LOCKED.html (280-1487)
   ═══════════════════════════════════════════════ */
(function(){
const BLOCKS = [
  {
    id: "BB_IMG_CAPTURE",
    name: "Image Capture Quality",
    weight: 0.4,
    description: "Whether captured images are valid, geotagged, overlapping, and consistent enough for photogrammetric reconstruction."
  },
  {
    id: "BB_ROVER_GNSS",
    name: "Rover GNSS Quality",
    weight: 0.3,
    description: "Whether the rover RINEX file is complete, clean, and sufficient to enable PPK correction."
  },
  {
    id: "BB_MISSION_EXEC",
    name: "Mission Execution Quality",
    weight: 0.2,
    description: "Whether the drone flew the planned mission with consistent altitude, GSD, and acceptable wind conditions."
  },
  {
    id: "BB_CAL_CONF",
    name: "Camera Calibration Confidence",
    weight: 0.1,
    description: "Whether camera calibration is from a known source, matches the camera used, and is recent enough."
  }
];

const GLOBAL_GATE_CONDITION = "(L3I_IMG_001 image_validity_score == 0) OR (L3I_GNSS_001 rover_coverage_score == 0) OR (L3I_FC_001 mission_coverage_score == 0)";

// ============================================================
// INDICATOR LIBRARY — single source of truth (Q1, Q2, Q4 locks applied)
// ============================================================
const INDICATOR_LIBRARY = {
  L3I_IMG_001: {
    id: "L3I_IMG_001",
    num: "#01",
    block: "BB_IMG_CAPTURE",
    weight: 0.34,
    name: "Image validity",
    fullName: "image_validity_score",
    is_critical_path: true,
    gate_scope: "chain_level",
    verified_statement: "All captured images are valid and usable for processing.",
    bands: [
      {
        score_range: [
          95,
          100
        ],
        level: "good",
        label: "\u226599% of captured images are valid",
        impact: null,
        actions: null
      },
      {
        score_range: [
          70,
          94
        ],
        level: "good",
        label: "95-99% valid \u2014 minor losses acceptable",
        impact: null,
        actions: null
      },
      {
        score_range: [
          30,
          69
        ],
        level: "review",
        label: "85-95% valid \u2014 significant image losses",
        impact: "A meaningful fraction of captured images are corrupted or unusable. Coverage may be reduced; processing may need to compensate with reduced overlap margin.",
        actions: [
          "Verify which images failed and why (storage, sensor, transmission)",
          "Check whether failed images create coverage gaps",
          "Inspect camera firmware and storage card health for next flight"
        ]
      },
      {
        score_range: [
          0,
          29
        ],
        level: "resurvey",
        label: "<85% valid (HARD GATE)",
        impact: "Too many images failed for the survey to be salvageable. Coverage gaps will be unrecoverable; the flight must be re-flown.",
        actions: [
          "Refly the survey with verified camera and storage hardware",
          "Inspect camera/storage system for systemic faults before refly",
          "Verify firmware versions match drone and camera spec"
        ]
      }
    ],
    derivation: "Hard gate at 30 reflects industry threshold for unrecoverable image loss (>15% lost). Below this, coverage cannot be maintained even with aggressive overlap.",
    flag: "CRITICAL_IMAGE_FAILURE"
  },
  L3I_IMG_002: {
    id: "L3I_IMG_002",
    num: "#02",
    block: "BB_IMG_CAPTURE",
    weight: 0.27,
    name: "Image geotag",
    fullName: "image_geotag_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Every image carries a valid GPS geotag from the rover.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "All images geotagged with rover-derived positions",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          84
        ],
        level: "review",
        label: "Some images missing geotags or using onboard GPS only",
        impact: "Images without rover-derived geotags rely on the drone's autonomous GPS \u2014 less accurate. PPK correction skipped for those frames; positioning weaker.",
        actions: [
          "Verify rover-to-camera time sync was active during flight",
          "Check log for events that disabled geotag injection",
          "Confirm next flight maintains continuous rover-camera link"
        ]
      },
      {
        score_range: [
          0,
          39
        ],
        level: "review",
        label: "Majority of images missing geotags",
        impact: "Most images lack rover-derived position. PPK correction provides little uplift; output accuracy may be similar to autonomous GPS (~3-5m).",
        actions: [
          "Inspect rover-camera sync configuration",
          "Review time-sync calibration procedure",
          "Consider whether this dataset meets accuracy requirements before processing"
        ]
      }
    ],
    derivation: "Geotag injection from rover is the basis for PPK position uplift. Coverage threshold 85 reflects minimum for PPK to add meaningful accuracy.",
    flag: null
  },
  L3I_IMG_003: {
    id: "L3I_IMG_003",
    num: "#03",
    block: "BB_IMG_CAPTURE",
    weight: 0.18,
    name: "Image overlap",
    fullName: "image_overlap_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Captured imagery has sufficient forward and side overlap for reconstruction.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Forward \u226575% AND side \u226565% (or mission-specified higher)",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          84
        ],
        level: "review",
        label: "Overlap below planned but within reconstructable range",
        impact: "Reduced overlap means weaker tie-point density. Reconstruction may have holes in low-texture areas (water, sand, snow, asphalt).",
        actions: [
          "Inspect orthomosaic for holes after processing",
          "Increase planned overlap for similar future surveys",
          "Review whether wind or speed affected actual capture density"
        ]
      },
      {
        score_range: [
          0,
          39
        ],
        level: "review",
        label: "Overlap below reconstructable range",
        impact: "Overlap too low to reconstruct reliably. Output orthomosaic and DSM may have significant holes or distortions.",
        actions: [
          "Check whether speed during flight was higher than planned",
          "Consider re-flying critical areas with tighter overlap",
          "Verify mission planner overlap settings for next flight"
        ]
      }
    ],
    derivation: "75/65 forward/side overlap from industry photogrammetric standards (Agisoft, Pix4D documentation). Below 60/45 reconstruction quality degrades sharply.",
    flag: "INSUFFICIENT_OVERLAP"
  },
  L3I_IMG_004: {
    id: "L3I_IMG_004",
    num: "#04",
    block: "BB_IMG_CAPTURE",
    weight: 0.12,
    name: "Image format",
    fullName: "image_format_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "All images are in a consistent supported format.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Single supported format (JPEG/TIFF/RAW), consistent across all images",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "Mixed formats or unsupported variants present",
        impact: "Format inconsistency can disrupt processing pipelines that expect uniform input. Mixed JPEG/RAW can cause exposure inconsistencies in the orthomosaic.",
        actions: [
          "Verify camera was set to single output format for the survey",
          "If mixed, consider whether to process formats separately",
          "Standardize camera config for future surveys"
        ]
      }
    ],
    derivation: "Format mixing is rare with modern cameras; flag is a hygiene check rather than a quality concern.",
    flag: "MIXED_IMAGE_FORMAT"
  },
  L3I_IMG_005: {
    id: "L3I_IMG_005",
    num: "#05",
    block: "BB_IMG_CAPTURE",
    weight: 0.09,
    name: "Exposure consistency",
    fullName: "image_exposure_consistency_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Image exposure stays consistent across the flight.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Exposure variance within camera auto-exposure tolerance",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "minor",
        label: "High exposure variation across flight (hygiene signal)",
        impact: "Cloud cover or terrain shadows during flight caused exposure variations. Orthomosaic may show patches of brighter/darker areas. Reconstruction still works.",
        actions: [
          "Inspect ortho for visible exposure seams",
          "Consider flying under more uniform lighting next time",
          "Apply post-process color matching if needed"
        ]
      }
    ],
    derivation: "Q-DRONE-5 alignment: exposure variation is a hygiene signal, not a quality killer. Downgraded to minor (audit-only).",
    flag: "HIGH_EXPOSURE_VARIATION"
  },
  L3I_GNSS_001: {
    id: "L3I_GNSS_001",
    num: "#06",
    block: "BB_ROVER_GNSS",
    weight: 0.3,
    name: "Rover coverage",
    fullName: "rover_coverage_score",
    is_critical_path: true,
    gate_scope: "chain_level",
    verified_statement: "Rover recorded continuously through the entire flight with adequate buffer.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Rover covered full flight + \u226560s buffer either side",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          84
        ],
        level: "review",
        label: "Rover covered flight but buffer is short or partial",
        impact: "Rover started recording too close to takeoff or stopped too soon after landing. PPK convergence may be incomplete at flight start; positions near edges may be weaker.",
        actions: [
          "Start rover 2-3 minutes before takeoff next time",
          "Stop rover only after motors off (60s post-landing)",
          "Inspect early/late epoch sigma in processed output"
        ]
      },
      {
        score_range: [
          0,
          0
        ],
        level: "resurvey",
        label: "Rover did not cover flight window (HARD GATE)",
        impact: "Rover RINEX has a gap during the flight. PPK cannot correct those flight epochs; positioning falls back to autonomous GPS (~3-5m accuracy).",
        actions: [
          "Refly with rover recording verified before takeoff",
          "Inspect rover hardware (battery, storage, antenna connection)",
          "Set up rover recording 2-3 minutes before mission planned start"
        ]
      }
    ],
    derivation: "Q-DRONE-2 promotion to chain-level hard gate. Without rover observations during flight, PPK has no reference data \u2014 output is autonomous-GPS accuracy regardless of how clean everything else is.",
    flag: "RINEX_CRITICAL_GAP"
  },
  L3I_GNSS_002: {
    id: "L3I_GNSS_002",
    num: "#07",
    block: "BB_ROVER_GNSS",
    weight: 0.25,
    name: "Rover signal quality",
    fullName: "rover_signal_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Rover maintained strong signal-to-noise throughout the flight.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "C/N0 consistently high (\u226540 dB-Hz on most satellites)",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          74
        ],
        level: "review",
        label: "Moderate C/N0 \u2014 signal partially degraded",
        impact: "Rover signal degraded during portions of the flight. Affected epochs may have higher position uncertainty even after PPK.",
        actions: [
          "Check rover antenna mounting \u2014 direct line of sight to sky?",
          "Inspect for interference sources (radio, power lines, drone radio at low altitude)",
          "Verify rover antenna is the right band for receiver"
        ]
      },
      {
        score_range: [
          0,
          39
        ],
        level: "review",
        label: "Poor C/N0 \u2014 significant signal issues",
        impact: "Rover signal was severely compromised. PPK output likely has elevated position uncertainty; some epochs may not converge.",
        actions: [
          "Inspect environment for interference (high-voltage lines, radio masts)",
          "Replace rover antenna or cable if recurring",
          "Choose less-obstructed setup location for next flight"
        ]
      }
    ],
    derivation: "C/N0 thresholds from GNSS receiver documentation. 40 dB-Hz is typical 'strong signal' baseline for survey-grade receivers.",
    flag: "POOR_SKY_VIEW_DURING_FLIGHT"
  },
  L3I_GNSS_003: {
    id: "L3I_GNSS_003",
    num: "#08",
    block: "BB_ROVER_GNSS",
    weight: 0.15,
    name: "Rover frequency",
    fullName: "rover_frequency_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Rover recorded at sufficient frequency (e.g., 5Hz) to support fast-moving drone.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "5Hz or higher recording rate",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "Recording rate below 5Hz (drone moves faster than rover samples)",
        impact: "Rover sampled too slowly for drone speed. Image geotag interpolation introduces position error, especially at flight turns.",
        actions: [
          "Set rover to \u22655Hz recording for next flight",
          "Verify rover receiver capability and configuration",
          "Consider whether this dataset meets accuracy requirements"
        ]
      }
    ],
    derivation: "5Hz is industry standard for survey drones flying \u226415 m/s. Higher speeds require proportionally higher rover rates.",
    flag: null
  },
  L3I_GNSS_004: {
    id: "L3I_GNSS_004",
    num: "#09",
    block: "BB_ROVER_GNSS",
    weight: 0.1,
    name: "Rover continuity",
    fullName: "rover_continuity_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Rover observations are continuous with minimal cycle slips.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Minimal cycle slips, no gaps >5s",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "review",
        label: "Cycle slips or gaps >5s detected",
        impact: "Rover lost lock briefly during flight. PPK must re-converge after each event; position accuracy reduced in those windows.",
        actions: [
          "Inspect rover environment for signal blockage during flight",
          "Check rover antenna stability (vibration, wind shaking)",
          "Position next setup with clearer sky view"
        ]
      }
    ],
    derivation: "Cycle slip tolerance threshold from PPK processing best practice.",
    flag: null
  },
  L3I_GNSS_005: {
    id: "L3I_GNSS_005",
    num: "#10",
    block: "BB_ROVER_GNSS",
    weight: 0.1,
    name: "Rover acquisition",
    fullName: "rover_acquisition_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Rover acquired satellites within normal startup time.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "<3 min \u2014 normal acquisition",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "minor",
        label: ">3 min \u2014 slow acquisition (hygiene signal)",
        impact: "Rover was slow to lock satellites. Usually cold start. Recurring slowness can indicate receiver health issues but rarely affects this flight's data.",
        actions: [
          "Check rover antenna mounting and view of sky",
          "Verify firmware and battery state",
          "Service receiver if slow acquisition recurs"
        ]
      }
    ],
    derivation: "Following base_station Q4 / Control Point Q-CP pattern: acquisition slowness is hygiene signal, not deliverable-quality.",
    flag: "SLOW_ROVER_ACQUISITION"
  },
  L3I_GNSS_006: {
    id: "L3I_GNSS_006",
    num: "#11",
    block: "BB_ROVER_GNSS",
    weight: 0.1,
    name: "Rover PDOP",
    fullName: "rover_pdop_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Strong satellite geometry during flight \u2014 PDOP within standard ranges.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "PDOP <4 throughout flight",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "review",
        label: "PDOP >4 during flight",
        impact: "Satellite geometry was suboptimal during flight. PPK still works but position uncertainty is elevated; sigma values higher than nominal.",
        actions: [
          "Use mission planner with satellite forecast for future flights",
          "Reschedule flight if PDOP forecast >6 for accuracy-critical work",
          "Check processed sigma values for elevated uncertainty"
        ]
      }
    ],
    derivation: "Standard PDOP categorization from GNSS handbooks.",
    flag: null
  },
  L3I_FC_001: {
    id: "L3I_FC_001",
    num: "#12",
    block: "BB_MISSION_EXEC",
    weight: 0.3,
    name: "Mission coverage",
    fullName: "mission_coverage_score",
    is_critical_path: true,
    gate_scope: "chain_level",
    verified_statement: "Drone flew the full planned mission area.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "\u226599% of planned mission area covered",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          84
        ],
        level: "review",
        label: "95-99% covered \u2014 minor gap",
        impact: "Small portion of the planned area was not flown. Output deliverable will have a corresponding gap; check whether the missed area was operationally important.",
        actions: [
          "Identify which polygon edge was missed and why",
          "Re-fly missing area if critical",
          "Verify mission was correctly uploaded next time"
        ]
      },
      {
        score_range: [
          0,
          0
        ],
        level: "resurvey",
        label: "<95% of planned area covered (HARD GATE)",
        impact: "Significant portion of the planned area was not surveyed. Deliverable is fundamentally incomplete; re-fly required.",
        actions: [
          "Refly with mission verified on drone before takeoff",
          "Check whether early battery, weather, or operator abort caused short flight",
          "Allocate adequate battery margin for similar future missions"
        ]
      }
    ],
    derivation: "Q-DRONE-2 promotion to chain-level hard gate. <95% coverage means deliverable is fundamentally incomplete regardless of other quality.",
    flag: "COVERAGE_GAP"
  },
  L3I_FC_002: {
    id: "L3I_FC_002",
    num: "#13",
    block: "BB_MISSION_EXEC",
    weight: 0.22,
    name: "GSD consistency",
    fullName: "mission_gsd_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Ground Sample Distance (GSD) is consistent with mission spec.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "GSD within \u00b110% of planned value",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "GSD deviates from planned by more than 10%",
        impact: "Actual ground resolution differs from planned. Output may not meet specified resolution for client deliverable.",
        actions: [
          "Inspect altitude logs \u2014 was drone too high or too low?",
          "Verify mission altitude was correctly entered",
          "Recalculate deliverable resolution claim against actual GSD"
        ]
      }
    ],
    derivation: "\u00b110% tolerance from photogrammetric practice \u2014 wider variance affects output GSD claims.",
    flag: null
  },
  L3I_FC_003: {
    id: "L3I_FC_003",
    num: "#14",
    block: "BB_MISSION_EXEC",
    weight: 0.22,
    name: "Actual overlap",
    fullName: "mission_overlap_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Actual flown overlap matches mission specification.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Actual overlap within \u00b15% of planned",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "Actual overlap deviates from planned by more than 5%",
        impact: "Speed during flight differed from plan, changing capture spacing. Reconstruction may be weaker in affected regions.",
        actions: [
          "Check whether wind affected actual ground speed",
          "Inspect ortho/DSM for weak-reconstruction patches",
          "Tighten planned overlap to account for typical wind"
        ]
      }
    ],
    derivation: "Couples L3I_IMG_003 image overlap \u2014 same concern measured from different sources (image inter-distance vs flight log).",
    flag: null
  },
  L3I_FC_004: {
    id: "L3I_FC_004",
    num: "#15",
    block: "BB_MISSION_EXEC",
    weight: 0.13,
    name: "Flight altitude consistency",
    fullName: "mission_altitude_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Flight altitude held within mission tolerance.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Altitude variance <5m from planned",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "Altitude variance >5m",
        impact: "Drone climbed or descended significantly during flight. GSD inconsistent across the dataset; reconstruction may show stretching or compression artifacts.",
        actions: [
          "Inspect altitude profile in flight log",
          "Check terrain follow setting \u2014 was AGL or AMSL used correctly?",
          "Verify no GPS altitude glitches in flight log"
        ]
      }
    ],
    derivation: "5m altitude tolerance from typical photogrammetric variance budget.",
    flag: "HIGH_ALTITUDE_VARIANCE"
  },
  L3I_FC_005: {
    id: "L3I_FC_005",
    num: "#16",
    block: "BB_MISSION_EXEC",
    weight: 0.05,
    name: "Wind condition",
    fullName: "wind_condition_score",
    is_critical_path: false,
    gate_scope: "none",
    null_band_supported: true,
    verified_statement: "Wind conditions during flight were within drone operating spec.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Wind \u22648 m/s \u2014 within typical drone spec",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          74
        ],
        level: "review",
        label: "Wind 8-12 m/s \u2014 drone struggling against wind",
        impact: "High wind during flight stressed the drone's stabilization. Actual ground track may have deviated from plan; image blur or off-axis attitude possible.",
        actions: [
          "Inspect flight log for unusual roll/pitch values",
          "Check images for motion blur",
          "Postpone flights when forecast wind exceeds drone spec"
        ]
      },
      {
        score_range: [
          0,
          39
        ],
        level: "review",
        label: "Wind >12 m/s \u2014 flight beyond drone spec",
        impact: "Wind exceeded drone operational limit. Flight integrity compromised; data quality likely degraded.",
        actions: [
          "Refly under calmer conditions",
          "Check forecast vs actual wind for future trip planning",
          "Consider drone class for high-wind operations"
        ]
      },
      {
        score_range: [
          null,
          null
        ],
        level: "null",
        label: "Wind data unavailable (Open-Meteo API failure)",
        impact: "Could not verify wind conditions during this flight. This is an infrastructure limitation, not a flight quality concern.",
        actions: [
          "No action required \u2014 indicator returns null and does not affect overall score",
          "Flight quality is judged from other indicators",
          "If wind verification is critical, retry API lookup or check independent wind station"
        ]
      }
    ],
    derivation: "Q-DRONE-4 introduces indicator-level null pattern. When Open-Meteo API unavailable, returns null + WIND_API_FALLBACK flag. Block aggregation renormalizes across measured indicators only. 8/12 m/s thresholds from typical drone manufacturer specs.",
    flag: "HIGH_WIND_SURVEY"
  },
  L3I_FC_006: {
    id: "L3I_FC_006",
    num: "#17",
    block: "BB_MISSION_EXEC",
    weight: 0.05,
    name: "Altitude consistency (in-block)",
    fullName: "mission_altitude_consistency_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Altitude held steady throughout the flight with minimal oscillation.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Altitude oscillation <2m per flight line",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "minor",
        label: "Altitude oscillation >2m (hygiene signal)",
        impact: "Drone bobbed in altitude during flight. Usually wind-related. Generally doesn't affect output but creates uneven GSD.",
        actions: [
          "Cross-reference with wind during flight",
          "Consider stiffer flight controller tuning if recurring",
          "Verify GPS altitude not glitchy"
        ]
      }
    ],
    derivation: "Hygiene signal, downgraded to minor \u2014 affects appearance but rarely affects measurement quality.",
    flag: null
  },
  L3I_FC_007: {
    id: "L3I_FC_007",
    num: "#18",
    block: "BB_MISSION_EXEC",
    weight: 0.03,
    name: "Mission completion",
    fullName: "mission_completion_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Mission completed cleanly without operator abort.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Mission completed normally, no operator interrupt",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          74
        ],
        level: "minor",
        label: "Mission interrupted but completed (audit hygiene)",
        impact: "Operator paused or restarted the mission. Generally doesn't affect output, but worth knowing for incident review.",
        actions: [
          "Note reason for interrupt in flight log if not recorded",
          "Verify no data gap at interrupt point",
          "Consider operator training if interrupts are recurring"
        ]
      }
    ],
    derivation: "Hygiene/audit signal. Downgraded to minor.",
    flag: null
  },
  L3I_CAL_001: {
    id: "L3I_CAL_001",
    num: "#19",
    block: "BB_CAL_CONF",
    weight: 0.5,
    name: "Calibration source",
    fullName: "calibration_source_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Camera was calibrated with a pre-existing lab calibration file.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Lab-calibrated camera with current calibration file",
        impact: null,
        actions: null
      },
      {
        score_range: [
          50,
          84
        ],
        level: "review",
        label: "Self-calibrated lens \u2014 no pre-existing calibration",
        impact: "Photogrammetry software is solving camera intrinsics from the imagery itself. Works for most cases, but adds uncertainty especially in non-flat terrain.",
        actions: [
          "Inspect self-calibration outputs for outlier focal length or distortion values",
          "Consider pre-calibrating camera for high-accuracy work",
          "Note in deliverable that self-calibration was used"
        ]
      },
      {
        score_range: [
          0,
          49
        ],
        level: "review",
        label: "No calibration file and self-calibration also failed",
        impact: "Both pre-calibration and self-calibration unsuccessful. Photogrammetric output reliability is uncertain; expect elevated residuals.",
        actions: [
          "Pre-calibrate camera in lab before next survey",
          "Inspect reconstruction residuals carefully",
          "Consider re-flying with calibrated camera if accuracy is critical"
        ]
      }
    ],
    derivation: "Q-DRONE-5 lock: self-calibration stays material/review. Operator decides whether to insist on pre-calibration based on accuracy requirements.",
    flag: "NO_CALIBRATION_FILE"
  },
  L3I_CAL_002: {
    id: "L3I_CAL_002",
    num: "#20",
    block: "BB_CAL_CONF",
    weight: 0.35,
    name: "Calibration match",
    fullName: "calibration_match_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Calibration file matches the camera that captured the imagery.",
    bands: [
      {
        score_range: [
          85,
          100
        ],
        level: "good",
        label: "Camera model and serial match calibration file",
        impact: null,
        actions: null
      },
      {
        score_range: [
          0,
          84
        ],
        level: "review",
        label: "Camera doesn't match calibration file",
        impact: "Calibration file is for a different camera. Using it introduces systematic distortion bias. Better to self-calibrate than use wrong file.",
        actions: [
          "Verify which physical camera was used vs calibration file",
          "Replace calibration file with correct one or disable",
          "Re-process with correct calibration if available"
        ]
      }
    ],
    derivation: "Camera-calibration mismatch is more harmful than no calibration. Reviewer-blocking flag.",
    flag: "CALIBRATION_CAMERA_MISMATCH"
  },
  L3I_CAL_003: {
    id: "L3I_CAL_003",
    num: "#21",
    block: "BB_CAL_CONF",
    weight: 0.15,
    name: "Calibration age",
    fullName: "calibration_age_score",
    is_critical_path: false,
    gate_scope: "none",
    verified_statement: "Camera calibration is recent enough to be reliable.",
    bands: [
      {
        score_range: [
          75,
          100
        ],
        level: "good",
        label: "Calibration \u22646 months old",
        impact: null,
        actions: null
      },
      {
        score_range: [
          40,
          74
        ],
        level: "minor",
        label: "Calibration 6-18 months old (audit hygiene)",
        impact: "Calibration is aging. Cameras drift over time due to handling and temperature; older calibrations are less reliable.",
        actions: [
          "Plan to re-calibrate in next maintenance cycle",
          "Inspect residuals for distortion patterns",
          "Document calibration date in deliverable metadata"
        ]
      },
      {
        score_range: [
          0,
          39
        ],
        level: "review",
        label: "Calibration >18 months old",
        impact: "Calibration may no longer reflect current camera state. Distortion model may be outdated; output accuracy could be degraded.",
        actions: [
          "Re-calibrate camera in lab before next survey",
          "Compare current and lab-calibration distortion parameters if available",
          "Note calibration age in deliverable QA notes"
        ]
      }
    ],
    derivation: "6-month / 18-month thresholds from photogrammetric best practice \u2014 cameras drift with handling.",
    flag: "CALIBRATION_POTENTIALLY_OUTDATED"
  }
};

const INDICATORS = Object.values(INDICATOR_LIBRARY);

// ============================================================
// SCENARIOS
// ============================================================
const SCENARIOS = [
  {
    id: "clean",
    name: "Clean Survey",
    desc: "Drone flew the planned mission cleanly with verified PPK setup",
    scores: {
      L3I_IMG_001: 100,
      L3I_IMG_002: 100,
      L3I_IMG_003: 100,
      L3I_IMG_004: 100,
      L3I_IMG_005: 100,
      L3I_GNSS_001: 100,
      L3I_GNSS_002: 100,
      L3I_GNSS_003: 100,
      L3I_GNSS_004: 100,
      L3I_GNSS_005: 100,
      L3I_GNSS_006: 100,
      L3I_FC_001: 100,
      L3I_FC_002: 100,
      L3I_FC_003: 100,
      L3I_FC_004: 100,
      L3I_FC_005: 100,
      L3I_FC_006: 100,
      L3I_FC_007: 100,
      L3I_CAL_001: 100,
      L3I_CAL_002: 100,
      L3I_CAL_003: 100
    }
  },
  {
    id: "review",
    name: "Mixed Quality (review_recommended)",
    desc: "Self-calibrated lens + moderate wind + slight overlap shortfall",
    scores: {
      L3I_IMG_001: 100,
      L3I_IMG_002: 100,
      L3I_IMG_003: 75,
      L3I_IMG_004: 100,
      L3I_IMG_005: 100,
      L3I_GNSS_001: 100,
      L3I_GNSS_002: 100,
      L3I_GNSS_003: 100,
      L3I_GNSS_004: 100,
      L3I_GNSS_005: 100,
      L3I_GNSS_006: 100,
      L3I_FC_001: 100,
      L3I_FC_002: 100,
      L3I_FC_003: 75,
      L3I_FC_004: 100,
      L3I_FC_005: 60,
      L3I_FC_006: 100,
      L3I_FC_007: 100,
      L3I_CAL_001: 60,
      L3I_CAL_002: 100,
      L3I_CAL_003: 100
    }
  },
  {
    id: "hard_gate",
    name: "Hard Gate Fired (mission coverage)",
    desc: "Battery died before mission complete \u2014 mission coverage hard gate fired",
    scores: {
      L3I_IMG_001: 100,
      L3I_IMG_002: 100,
      L3I_IMG_003: 100,
      L3I_IMG_004: 100,
      L3I_IMG_005: 100,
      L3I_GNSS_001: 100,
      L3I_GNSS_002: 100,
      L3I_GNSS_003: 100,
      L3I_GNSS_004: 100,
      L3I_GNSS_005: 100,
      L3I_GNSS_006: 100,
      L3I_FC_001: 0,
      L3I_FC_002: 100,
      L3I_FC_003: 100,
      L3I_FC_004: 100,
      L3I_FC_005: 100,
      L3I_FC_006: 100,
      L3I_FC_007: 100,
      L3I_CAL_001: 100,
      L3I_CAL_002: 100,
      L3I_CAL_003: 100
    }
  },
  {
    id: "wind_api_null",
    name: "Wind API Unavailable (indicator-level null)",
    desc: "Open-Meteo API failed \u2014 wind indicator returns null (Q-DRONE-4 pattern). Everything else clean.",
    scores: {
      L3I_IMG_001: 100,
      L3I_IMG_002: 100,
      L3I_IMG_003: 100,
      L3I_IMG_004: 100,
      L3I_IMG_005: 100,
      L3I_GNSS_001: 100,
      L3I_GNSS_002: 100,
      L3I_GNSS_003: 100,
      L3I_GNSS_004: 100,
      L3I_GNSS_005: 100,
      L3I_GNSS_006: 100,
      L3I_FC_001: 100,
      L3I_FC_002: 100,
      L3I_FC_003: 100,
      L3I_FC_004: 100,
      L3I_FC_005: 100,
      L3I_FC_006: 100,
      L3I_FC_007: 100,
      L3I_CAL_001: 100,
      L3I_CAL_002: 100,
      L3I_CAL_003: 100
    },
    null_indicators: [
      "L3I_FC_005"
    ]
  }
];

// ============================================================
// LIBRARY HELPERS
// ============================================================
function getBandForScore(indicator, score) {
  for (const band of indicator.bands) {
    const [lo, hi] = band.score_range;
    if (score >= lo && score <= hi) return band;
  }
  return indicator.bands[indicator.bands.length - 1];
}

function severityForBand(band) {
  if (band.level === "resurvey") return "critical";
  if (band.level === "review")   return "material";
  if (band.level === "minor")    return "minor";
  return "none";
}

function severityForScore(indicator, score) {
  return severityForBand(getBandForScore(indicator, score));
}

function scoreLevel(score) {
  if (score === 0) return "resurvey";
  if (score >= 75) return "good";
  if (score >= 50) return "review";
  return "resurvey";
}

// ============================================================
// SCORING
// ============================================================
function computeBlockScore(blockId, scores) {
  const inds = INDICATORS.filter(i => i.block === blockId);
  let totalW = 0, sumW = 0;
  for (const i of inds) {
    const s = scores[i.id];
    if (s === undefined) continue;
    totalW += i.weight;
    sumW += i.weight * s;
  }
  return totalW > 0 ? sumW / totalW : 0;
}

function checkHardGate(scores) {
  for (const i of INDICATORS) {
    if (i.is_critical_path && scores[i.id] === 0) return { fired: true, source: i };
  }
  return { fired: false, source: null };
}

function computeOverallScore(scores, nullIndicators) {
  nullIndicators = nullIndicators || [];
  const gate = checkHardGate(scores);
  if (gate.fired) return { score: 0, hardGate: true, gateSource: gate.source };

  let totalW = 0, sumW = 0;
  for (const b of BLOCKS) {
    const bs = computeBlockScore(b.id, scores, nullIndicators);
    totalW += b.weight;
    sumW += b.weight * bs;
  }
  return { score: totalW > 0 ? sumW / totalW : 0, hardGate: false };
}

function overallRecommendation(scores, nullIndicators) {
  nullIndicators = nullIndicators || [];
  const overall = computeOverallScore(scores, nullIndicators);
  if (overall.hardGate) return { rec: "resurvey", overall };

  for (const i of INDICATORS) {
    if (nullIndicators.includes(i.id)) continue;
    if (severityForScore(i, scores[i.id]) === "critical") return { rec: "resurvey", overall };
  }
  for (const i of INDICATORS) {
    if (nullIndicators.includes(i.id)) continue;
    if (severityForScore(i, scores[i.id]) === "material") return { rec: "review", overall };
  }
  return { rec: "good", overall };
}


/* ===========================================================================
   DRONE UI LAYER — modeled on the frozen Base v22 datum page.
   Assumes the LOCKED drone data+engine is already defined above:
     BLOCKS, INDICATOR_LIBRARY, INDICATORS, SCENARIOS, GLOBAL_GATE_CONDITION,
     getBandForScore, severityForBand, severityForScore, computeBlockScore,
     checkHardGate, computeOverallScore(scores,nul), overallRecommendation(scores,nul)
   All DOM ids are prefixed "dn-" so they never collide with the Base view.
   =========================================================================== */

/* recommendation copy — UI text (not chain content), drone-generic */
var REC_REASON={
  good:"All checks passed. Drone capture is clean and survey-grade — clear to proceed.",
  review:"A few indicators need a quick check before you proceed — see the flagged blocks below.",
  resurvey:"A hard gate fired. This capture can't produce usable output as-is and needs to be reflown."
};
var REC_LABEL={good:"GOOD TO GO",review:"REVIEW",resurvey:"RESURVEY"};
var REC_VERDICT_COLOR={good:"rgba(16,185,214,.9)",review:"rgba(232,228,218,.94)",resurvey:"var(--red)"};
var SCEN_SHORT={clean:"Clean",review:"Review",hard_gate:"Hard Gate",wind_api_null:"Wind N/A"};

function blockIndicators(blockId){return INDICATORS.filter(function(i){return i.block===blockId;});}
function isNull(id,nul){return (nul||[]).indexOf(id)>=0;}
function blockLevel(blockId,scores,nul){
  nul=nul||[];var worst="good";
  blockIndicators(blockId).forEach(function(i){
    if(isNull(i.id,nul))return;
    var lvl=getBandForScore(i,scores[i.id]).level;
    if(lvl==="resurvey")worst="resurvey";
    else if(lvl==="review"&&worst!=="resurvey")worst="review";
  });
  return worst;
}
function importance(ind){
  var b=BLOCKS.filter(function(x){return x.id===ind.block;})[0];
  var bw=b?b.weight:0;
  return (ind.is_critical_path?1000:0)+bw*ind.weight*100;
}
function pctRound(n){return Math.round(n);}
function statusText(level){return level==="good"?"OK":(level==="resurvey"?"Resurvey":"Review");}
function curNul(){return (currentScenario&&currentScenario.nullIndicators)||[];}

/* illustrative trend (sample, like Base); last point reflects the real review score */
var TREND=[
  {sid:"S-038",date:"Oct 25",score:90,anom:false},
  {sid:"S-039",date:"Nov 25",score:92,anom:false},
  {sid:"S-040",date:"Nov 25",score:84,anom:true,note:"Overlap shortfall on NE block"},
  {sid:"S-041",date:"Dec 25",score:93,anom:false},
  {sid:"S-042",date:"Dec 25",score:96,anom:false},
  {sid:"S-043",date:"Jan 26",score:94,anom:false},
  {sid:"S-044",date:"Jan 26",score:92,anom:false},
  {sid:"S-045",date:"Feb 26",score:95,anom:false},
  {sid:"S-046",date:"Mar 26",score:97,anom:false},
  {sid:"S-047",date:"May 26",score:95,anom:false}
];
var FLEET=[88,90,86,91,93,92,91,93,95,94];
var fleetOn=false;

/* pill positions around the render — extended to drone's larger blocks */
var POS={
  1:[[50,16]],
  2:[[26,30],[74,30]],
  3:[[24,40],[76,40],[50,84]],
  4:[[22,32],[78,32],[28,76],[72,76]],
  5:[[50,12],[20,34],[80,34],[30,82],[70,82]],
  6:[[22,20],[78,20],[14,52],[86,52],[34,84],[66,84]],
  7:[[50,10],[20,26],[80,26],[12,56],[88,56],[34,86],[66,86]]
};
function posFor(n){
  if(POS[n])return POS[n];
  var a=[];for(var i=0;i<n;i++){var ang=(i/n)*2*Math.PI-Math.PI/2;a.push([50+38*Math.cos(ang),50+40*Math.sin(ang)]);}
  return a;
}

var currentScenario=SCENARIOS.filter(function(s){return s.id==="review";})[0]||SCENARIOS[0];
var selected={};

/* ---- sparkline (left panel) ---- */
(function(){
  var svg=document.getElementById("dn-sparkSvg");
  if(!svg)return;
  var W=192,H=52,pL=2,pR=2,pT=5,pB=5;
  var n=TREND.length,mn=75,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*(W-pL-pR):(W-pL-pR)/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*(H-pT-pB)};
  var area="M "+pL+" "+(H-pB);
  TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});
  area+=" L "+(W-pR)+" "+(H-pB)+" Z";
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  var s='<path fill="url(#dn-spGrad)" d="'+area+'"/>';
  s+='<path fill="none" stroke="rgba(16,185,214,.5)" stroke-width="1" d="'+line+'"/>';
  TREND.forEach(function(d,i){var x=sx(i),y=sy(d.score);
    s+='<circle fill="'+(d.anom?"rgba(232,228,218,.4)":"rgba(16,185,214,.5)")+'" cx="'+x+'" cy="'+y+'" r="1.8"><title>'+d.sid+" · "+d.score+'</title></circle>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1" cx="'+lx+'" cy="'+ly+'" r="3.8"/>';
  svg.innerHTML+=s;
})();

/* ---- trend modal ---- */
function openTrend(){document.getElementById("dn-trendModal").classList.add("open");drawTrend();}
function closeTrend(){document.getElementById("dn-trendModal").classList.remove("open");}
function toggleFleet(){fleetOn=!fleetOn;document.getElementById("dn-fleetBtn").classList.toggle("on",fleetOn);drawTrend();}
function drawTrend(){
  var svg=document.getElementById("dn-trendSvg");
  var W=900,H=256,pL=44,pR=24,pT=14,pB=34;
  var iW=W-pL-pR,iH=H-pT-pB;
  var n=TREND.length,mn=40,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*iW:iW/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*iH};
  var s='<defs><linearGradient id="dn-tgGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(16,185,214,.25)"/><stop offset="100%" stop-color="rgba(16,185,214,.00)"/></linearGradient></defs>';
  [40,60,75,90,100].forEach(function(v){
    s+='<line class="tg-axis" x1="'+pL+'" y1="'+sy(v)+'" x2="'+(W-pR)+'" y2="'+sy(v)+'"/>';
    s+='<text class="tg-tick" x="'+(pL-6)+'" y="'+(sy(v)+3)+'" text-anchor="end">'+v+'</text>';
  });
  s+='<rect class="tg-band" x="'+pL+'" y="'+sy(100)+'" width="'+iW+'" height="'+(sy(90)-sy(100))+'"/>';
  s+='<text x="'+(W-pR+4)+'" y="'+(sy(90)+3)+'" font-family="IBM Plex Mono" font-size="8" fill="rgba(16,185,214,.28)">Survey</text>';
  s+='<text x="'+(W-pR+4)+'" y="'+(sy(75)+3)+'" font-family="IBM Plex Mono" font-size="8" fill="rgba(200,210,220,.16)">Eng.</text>';
  var area="M "+pL+" "+sy(mn);
  TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});
  area+=" L "+(W-pR)+" "+sy(mn)+" Z";
  s+='<path d="'+area+'" fill="url(#dn-tgGrad)"/>';
  if(fleetOn){var fp="";FLEET.forEach(function(v,i){fp+=(i===0?"M ":"L ")+sx(i)+" "+sy(v)+" "});
    s+='<path class="tg-fleet" d="'+fp+'"/>';
    s+='<text class="tg-lbl" x="'+(sx(FLEET.length-1)+5)+'" y="'+(sy(FLEET[FLEET.length-1])+3)+'">fleet median</text>';}
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  s+='<path class="tg-line" d="'+line+'"/>';
  TREND.forEach(function(d,i){var x=sx(i),y=sy(d.score);
    s+='<circle class="tg-pt'+(d.anom?" anom":"")+'" cx="'+x+'" cy="'+y+'" r="4.5"><title>'+d.sid+" · "+d.score+(d.note?" ("+d.note+")":"")+'</title></circle>';
    if(i%2===0||i===n-1)s+='<text class="tg-tick" x="'+x+'" y="'+(H-pB+13)+'" text-anchor="middle">'+d.date+'</text>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1"/>';
  s+='<text class="tg-lbl" x="'+(lx-7)+'" y="'+(ly-11)+'" text-anchor="end" fill="rgba(16,185,214,.6)">current · '+TREND[n-1].score+'</text>';
  svg.innerHTML=s;
}

function toggleBBSection(){
  document.getElementById("dn-bbSectionBody").classList.toggle("open");
  document.getElementById("dn-bbSectionIcon").classList.toggle("open");
}

/* ---- render ---- */
function scenRec(s){return overallRecommendation(s.scores,s.nullIndicators||[]).rec;}
function renderScenarioPicker(){
  var el=document.getElementById("dn-scnPick");if(!el)return;
  el.innerHTML=SCENARIOS.map(function(s){
    var on=s.id===currentScenario.id,rec=scenRec(s);
    var cls="scn-opt"+(on?" on":"");
    if(on&&rec==="review")cls+=" warn";
    if(on&&rec==="resurvey")cls+=" bad";
    return '<button class="'+cls+'" onclick="dsDrone.selectScenario(\''+s.id+'\')">'+(SCEN_SHORT[s.id]||s.name)+'</button>';
  }).join("");
}
function selectScenario(id){
  var s=SCENARIOS.filter(function(x){return x.id===id;})[0];if(!s)return;
  currentScenario=s;selected={};closeDrawer();renderAll();
}

function renderHeadline(){
  var scores=currentScenario.scores,nul=curNul();
  var rec=overallRecommendation(scores,nul);
  var overall=pctRound(rec.overall.score);
  document.getElementById("dn-scoreNum").innerHTML=overall+'<span class="pct">%</span>';
  document.getElementById("dn-scoreDelta").textContent=
    rec.rec==="resurvey"?"Hard gate — score forced to 0":"Weighted across "+BLOCKS.length+" blocks";
  var vt=document.getElementById("dn-mdVerdictText");if(vt)vt.textContent=REC_LABEL[rec.rec];
  var verdict=document.getElementById("dn-mdVerdict");if(verdict)verdict.style.color=REC_VERDICT_COLOR[rec.rec];
  document.getElementById("dn-mdReason").innerHTML=REC_REASON[rec.rec];
}

function renderBBCards(){
  var scores=currentScenario.scores,nul=curNul();
  var host=document.getElementById("dn-bbStripHead");
  host.innerHTML=BLOCKS.map(function(b,idx){
    var bs=pctRound(computeBlockScore(b.id,scores));
    var lvl=blockLevel(b.id,scores,nul);
    var cls="bb-card"+(lvl==="review"?" review":"")+(lvl==="resurvey"?" resurvey":"");
    var num="BB · 0"+(idx+1);
    var fillW=lvl==="good"?100:bs;
    var fillCol=lvl==="good"?"rgba(16,185,214,.38)":(lvl==="resurvey"?"rgba(201,64,64,.5)":"rgba(232,228,218,.18)");
    return '<div class="'+cls+'" id="dn-'+b.id+'">'+
        '<div class="bb-header"><div class="bb-h-left">'+
          '<div class="bb-num">'+num+'</div>'+
          '<div class="bb-name">'+b.name+'</div>'+
          '<div class="bb-weight">weight '+b.weight.toFixed(2)+'</div>'+
        '</div><div class="bb-h-right">'+
          '<div class="bb-score-sm">'+bs+'%</div>'+
          '<div class="bb-status-dot"></div>'+
        '</div></div>'+
        '<div class="bb-inner-always">'+
          '<div class="bb-bar"><div class="bb-bar-fill" style="width:'+fillW+'%;background:'+fillCol+'"></div></div>'+
          '<div class="bb-toggle-row" onclick="dsDrone.toggleBBIndicators(\''+b.id+'\')"><span class="bb-check"></span><span class="bb-toggle-text">Show indicators</span></div>'+
          '<div class="bb-status-full">'+statusText(lvl)+'</div>'+
          '<div class="bb-details" onclick="event.stopPropagation();dsDrone.openBBDetails(\''+b.id+'\')">Details ›</div>'+
        '</div>'+
      '</div>';
  }).join("");
  markActiveBB();
}

function renderIndicators(){
  var layer=document.getElementById("dn-indicatorLayer");if(!layer)return;
  var scores=currentScenario.scores,nul=curNul();
  var html=[];
  BLOCKS.forEach(function(b){
    if(!selected[b.id])return;
    var inds=blockIndicators(b.id);
    var pts=posFor(inds.length);
    inds.forEach(function(ind,i){
      var p=pts[i]||[50,75];
      if(isNull(ind.id,nul)){
        html.push('<div class="indicator-pill sev-pending" style="left:'+p[0]+'%;top:'+p[1]+'%"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">N/A</b></div>');
        return;
      }
      var lvl=getBandForScore(ind,scores[ind.id]).level;
      var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
      html.push('<div class="indicator-pill'+sev+'" style="left:'+p[0]+'%;top:'+p[1]+'%"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+scores[ind.id]+'</b></div>');
    });
  });
  layer.innerHTML=html.join("");
  layer.className="indicator-layer"+(html.length?" show":"");
}

function toggleBBIndicators(id){selected[id]=!selected[id];markActiveBB();renderIndicators();}
function markActiveBB(){BLOCKS.forEach(function(b){var el=document.getElementById("dn-"+b.id);if(el)el.classList.toggle("active",!!selected[b.id]);});}

/* ---- per-block Details drawer ---- */
function openBBDetails(blockId){
  selected[blockId]=true;markActiveBB();renderIndicators();
  var b=BLOCKS.filter(function(x){return x.id===blockId;})[0];
  var scores=currentScenario.scores,nul=curNul();
  var bs=pctRound(computeBlockScore(blockId,scores));
  var lvl=blockLevel(blockId,scores,nul);
  var inds=blockIndicators(blockId);
  var rows=inds.map(function(ind){
    var sc=scores[ind.id];
    var pending=isNull(ind.id,nul);
    var band=getBandForScore(ind,sc);
    var sevCls=pending?"":(band.level==="good"?"":(band.level==="resurvey"?"resurvey":(band.level==="minor"?"":"review")));
    var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
    var html='<div class="d-ind"><div class="d-ind-top">'+
      '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
      '<div class="d-ind-sc '+sevCls+'">'+(pending?"N/A":sc)+'</div></div>'+
      '<div class="d-ind-band">'+(pending?"Pending — data unavailable for this survey":(band.level==="good"?ind.verified_statement:band.label))+'</div>';
    if(!pending&&band.impact)html+='<div class="d-ind-impact">'+band.impact+'</div>';
    if(!pending&&band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+a+'</li>';}).join("")+'</ul>';
    // weighted contribution
    var contrib=pending?0:(ind.weight*sc);
    html+='<div class="d-deriv">in-block weight '+ind.weight.toFixed(2)+' · contributes '+(pending?"—":Math.round(contrib)+'/'+Math.round(ind.weight*100))+'</div>';
    if(ind.derivation)html+='<div class="d-deriv">'+ind.derivation+'</div>';
    return html+'</div>';
  }).join("");
  // limiting factor (lowest weighted-contribution non-null indicator)
  var lim=null,limv=1e9;
  inds.forEach(function(ind){if(isNull(ind.id,nul))return;var v=ind.weight*scores[ind.id];if(v<limv){limv=v;lim=ind;}});
  var limHtml=lim?'<div class="d-gate" style="color:rgba(232,228,218,.6);border-color:var(--line);background:rgba(255,255,255,.014)">Limiting factor: '+lim.name+'</div>':'';
  document.getElementById("dn-drawerBody").innerHTML=
    '<h2>'+b.name+'</h2>'+
    '<div class="d-score">'+bs+'<span>%</span></div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
    '<div class="d-narr">'+b.description+'</div>'+
    limHtml+
    '<div class="d-sec">Indicators</div>'+rows;
  openDrawer();
}

/* ---- Why drawer ---- */
function accPanel(ind,scores,kind,open){
  var band=getBandForScore(ind,scores[ind.id]);
  var scCls=kind==="resurvey"?"resurvey":(kind==="review"?"review":"");
  var tagTxt={verified:"Verified",review:"Review",resurvey:"Resurvey",noted:"Noted"}[kind];
  var body;
  if(kind==="verified"){
    body='<div class="acc-state">'+ind.verified_statement+'</div><div class="acc-evi">Evidence · '+band.label+'</div>';
  }else{
    body='<div class="acc-state">'+band.label+'</div>'+
         (band.impact?'<div class="d-ind-impact">'+band.impact+'</div>':'')+
         (band.actions?'<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+a+'</li>';}).join("")+'</ul>':'');
  }
  return '<div class="acc'+(open?" open":"")+'">'+
      '<div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
        '<span class="acc-chev">▶</span>'+
        '<span class="acc-name">'+ind.name+'</span>'+
        '<span class="acc-right"><span class="acc-sc '+scCls+'">'+scores[ind.id]+'</span>'+
          '</span>'+
      '</div>'+
      '<div class="acc-body"><div class="acc-inner">'+body+'</div></div>'+
    '</div>';
}
function setSection(sel,open){var rows=document.querySelectorAll(sel+" .acc");for(var i=0;i<rows.length;i++)rows[i].classList.toggle("open",open);}
function verifiedBlock(count,listHtml){
  var head='<div class="d-sec-row"><div class="d-sec verified">Verified<span class="d-sec-count">'+count+'</span></div></div>';
  if(count<=0) return head+'<div id="dn-verSec">'+listHtml+'</div>';
  var summary=(typeof INDICATORS!=='undefined'&&count===INDICATORS.length)?('All '+count+' indicators passed verification.'):(count+' indicators verified and in good standing.');
  return '<div class="d-sec-row"><div style="display:flex;align-items:baseline;gap:10px;flex:1;min-width:0"><span class="d-sec verified" style="margin:0;padding:0;border:0;flex-shrink:0">Verified</span><span class="d-empty" style="padding:0">'+summary+'</span></div><button class="d-ctrl" id="dn-verToggle" onclick="dsDrone.toggleVerified()" style="flex-shrink:0">+ More Details</button></div>'+'<div id="dn-verSec" style="display:none">'+listHtml+'</div>';
}
function toggleVerified(){
  var sec=document.getElementById('dn-verSec'),tog=document.getElementById('dn-verToggle');
  if(!sec||!tog)return;
  var open=(sec.style.display==='none');
  sec.style.display=open?'block':'none';
  tog.innerHTML=open?'\u2212 Show less':'+ More Details';
}
function openRecommendation(){
  var scores=currentScenario.scores,nul=curNul();
  var rec=overallRecommendation(scores,nul);
  var overall=pctRound(rec.overall.score);
  var actionable=[],verified=[],noted=[];
  INDICATORS.forEach(function(ind){
    if(isNull(ind.id,nul)){noted.push({ind:ind,kind:"noted"});return;}
    var sev=severityForScore(ind,scores[ind.id]);
    if(sev==="critical")actionable.push({ind:ind,kind:"resurvey",rank:0});
    else if(sev==="material")actionable.push({ind:ind,kind:"review",rank:1});
    else if(sev==="minor")noted.push({ind:ind,kind:"noted"});
    else verified.push({ind:ind,kind:"verified"});
  });
  actionable.sort(function(a,b){return a.rank-b.rank||importance(b.ind)-importance(a.ind);});
  verified.sort(function(a,b){return importance(b.ind)-importance(a.ind);});
  var gateHtml=rec.overall.hardGate?
    '<div class="d-gate">HARD GATE — '+rec.overall.gateSource.name+' scored 0, forcing overall to 0. Gate: '+GLOBAL_GATE_CONDITION+'</div>':'';
  var verifiedOpen=actionable.length===0;
  var actHtml=actionable.length
    ? actionable.map(function(f){return accPanel(f.ind,scores,f.kind,true);}).join("")
    : '<div class="d-empty">Nothing to action — no Review or Resurvey findings.</div>';
  var notedHtml=noted.map(function(f){
    if(isNull(f.ind.id,nul)){
      return '<div class="acc"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
        '<span class="acc-chev">▶</span><span class="acc-name">'+f.ind.name+'</span>'+
        '<span class="acc-right"><span class="acc-sc">N/A</span></span></div>'+
        '<div class="acc-body"><div class="acc-inner"><div class="acc-state">Data unavailable for this survey (e.g. external source offline). Excluded from scoring; revisit when available.</div></div></div></div>';
    }
    return accPanel(f.ind,scores,"noted",false);
  }).join("");
  var verHtml=verified.length
    ? verified.map(function(f){return accPanel(f.ind,scores,"verified",false);}).join("")
    : '<div class="d-empty">No checks passed cleanly.</div>';
  document.getElementById("dn-drawerBody").innerHTML=
    '<h2>Why '+REC_LABEL[rec.rec].replace("GOOD TO GO","Good to go")+'?</h2>'+
    
    
    '<div class="d-narr">'+REC_REASON[rec.rec]+'</div>'+gateHtml+
    '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+actionable.length+'</span></div>'+
      '<div class="d-ctrls"><button class="d-ctrl" onclick="dsDrone.setSection(\'#dn-actSec\',true)">Expand all</button>'+
      '<button class="d-ctrl" onclick="dsDrone.setSection(\'#dn-actSec\',false)">Collapse all</button></div></div>'+
    '<div id="dn-actSec">'+actHtml+notedHtml+'</div>'+
    verifiedBlock(verified.length, verHtml);
  openDrawer();
}

function openDrawer(){document.getElementById("dn-drawer").classList.add("open");}
function closeDrawer(){document.getElementById("dn-drawer").classList.remove("open");}

var DRONE_API_READY=false;
function renderDroneNoApi(msg){
  var score=document.getElementById("dn-scoreNum"); if(score) score.innerHTML='<span style="font-size:28px;opacity:.45;letter-spacing:.1em">NO API DATA</span>';
  var delta=document.getElementById("dn-scoreDelta"); if(delta) delta.textContent=msg||"Start the API and refresh the database.";
  var reason=document.getElementById("dn-mdReason"); if(reason) reason.textContent=msg||"No Drone API data loaded.";
  var pick=document.getElementById("dn-scnPick"); if(pick) pick.innerHTML="";
  var cards=document.getElementById("dn-bbStripHead"); if(cards) cards.innerHTML='<div class="d-empty">No Drone records returned by the API.</div>';
  var layer=document.getElementById("dn-indicatorLayer"); if(layer){layer.innerHTML="";layer.className="indicator-layer";}
}
function renderAll(){
  if(!DRONE_API_READY){renderDroneNoApi();return;}
  renderScenarioPicker();renderHeadline();renderBBCards();renderIndicators();
}

var REAL_OVERALL=computeOverallScore(currentScenario.scores,curNul()).score;
window.dsDrone={openTrend:openTrend,closeTrend:closeTrend,toggleFleet:toggleFleet,
  toggleBBSection:toggleBBSection,selectScenario:selectScenario,
  toggleBBIndicators:toggleBBIndicators,openBBDetails:openBBDetails,
  openRecommendation:openRecommendation,closeDrawer:closeDrawer,
  setSection:setSection,toggleVerified:toggleVerified,render:renderAll,
  refreshApi:function(){ if(!DRONE_API_READY) loadLiveDroneScores(); },
  realScore:REAL_OVERALL};

var DRONE_API_URL = loopApiUrl("/api/drone/indicators");
var DRONE_API_RETRY_COUNT=0;
var DRONE_API_RETRY_MAX=240;
var DRONE_API_RETRY_MS=3000;
var DRONE_API_LOADING=false;

function droneShowLoadingState(){
  var el=document.getElementById("dn-scoreNum");
  if(el) el.innerHTML='<span style="font-size:28px;opacity:.4;letter-spacing:.1em">LOADING</span>';
}

function droneShowErrorBadge(msg){
  var badge=document.createElement("div");
  badge.style.cssText=[
    "position:fixed;bottom:18px;left:50%;transform:translateX(-50%)",
    "background:rgba(201,64,64,.18);border:1px solid rgba(201,64,64,.4)",
    "color:rgba(232,228,218,.7);font-family:var(--fm);font-size:10px",
    "letter-spacing:.12em;padding:6px 14px;border-radius:2px;z-index:9999",
    "pointer-events:none"
  ].join(";");
  badge.textContent="DRONE API UNAVAILABLE - no live data loaded  ·  "+msg;
  document.body.appendChild(badge);
  setTimeout(function(){ badge.remove(); },6000);
}

function injectLiveDroneScenario(indicators){
  var scores={};
  indicators.forEach(function(item){
    var id=item.id||item.indicator_id;
    if(!id) return;
    scores[id]=item.score;
    var lib=(typeof INDICATOR_LIBRARY!=="undefined")?INDICATOR_LIBRARY[id]:null;
    if(lib){
      lib._apiScore=item.score;
      lib._liveInputs=item.input_values || {input_value:item.input_value};
      lib._liveBand=item.band_matched||null;
      lib._liveCondition=item.condition||item.condition_evaluated||null;
    }
  });
  var liveScenario={id:"live",name:"Live",scores:scores,nullIndicators:[],_live:true};
  SCENARIOS.splice(0, SCENARIOS.length, liveScenario);
  currentScenario=liveScenario;
  DRONE_API_READY=true;
  selected={};
  closeDrawer();
}

function loadLiveDroneScores(){
  if(DRONE_API_LOADING) return;
  DRONE_API_LOADING=true;
  droneShowLoadingState();
  fetch(withCacheBust(DRONE_API_URL),{cache:'no-store'})
    .then(function(res){
      if(!res.ok) throw new Error("HTTP "+res.status);
      return res.json();
    })
    .then(function(data){
      DRONE_API_LOADING=false;
      var indicators=data.indicators||data;
      if(!Array.isArray(indicators)||!indicators.length) throw new Error("empty indicators array");
      DRONE_API_RETRY_COUNT=0;
      injectLiveDroneScenario(indicators);
      window.dsDrone.realScore=computeOverallScore(currentScenario.scores,curNul()).score;
      renderAll();
    })
    .catch(function(err){
      DRONE_API_LOADING=false;
      if(DRONE_API_RETRY_COUNT===0 || DRONE_API_RETRY_COUNT%20===0) droneShowErrorBadge(err.message||String(err));
      DRONE_API_READY=false;
      renderDroneNoApi(err.message||String(err));
      if(DRONE_API_RETRY_COUNT<DRONE_API_RETRY_MAX){
        DRONE_API_RETRY_COUNT++;
        setTimeout(loadLiveDroneScores,DRONE_API_RETRY_MS);
      }
    });
}

loadLiveDroneScores();

})();

/* ── DRONE → GLOBAL CONFIDENCE wiring (single real state = review/Mixed Quality) ── */
(function(){
  var real = (window.dsDrone && typeof window.dsDrone.realScore==='number')
             ? Math.round(window.dsDrone.realScore) : 95;
  if(typeof SUB_CAPTURE_DRONE!=='undefined') SUB_CAPTURE_DRONE.score = real;
  // Capture universe derived from its subsystem real scores (Base already set to 87 earlier).
  var W={drone:0.35, base:0.30, gcp:0.20, preproc:0.15};
  var sc={
    drone:real,
    base:(typeof SUB_CAPTURE_BASE!=='undefined')?SUB_CAPTURE_BASE.score:87,
    gcp:(typeof SUB_CAPTURE_GCP!=='undefined')?SUB_CAPTURE_GCP.score:78,
    preproc:(typeof SUB_CAPTURE_PREPROC!=='undefined')?SUB_CAPTURE_PREPROC.score:90
  };
  var capScore=Math.round(W.drone*sc.drone+W.base*sc.base+W.gcp*sc.gcp+W.preproc*sc.preproc);
  if(typeof ONTOLOGY!=='undefined' && ONTOLOGY.universes && ONTOLOGY.universes[0]){
    ONTOLOGY.universes[0].score=capScore;
    if(typeof GATES!=='undefined' && GATES[0]){
      GATES[0].score=capScore;
      if(GATES[0].universe) GATES[0].universe.score=capScore;
    }
    var nOJS=Math.round(
      ONTOLOGY.universes[0].score*ONTOLOGY.universes[0].weight +
      ONTOLOGY.universes[1].score*ONTOLOGY.universes[1].weight +
      ONTOLOGY.universes[2].score*ONTOLOGY.universes[2].weight);
    var ms=document.getElementById('ms-num');
    if(ms) ms.innerHTML=nOJS+'<span style="font-size:.28em;font-weight:700;color:rgba(235,242,248,.38);vertical-align:super;line-height:0;">%</span>';
    var st=document.getElementById('sentence-text');
    if(st) st.innerHTML='Pitpack 4 scored <strong>'+nOJS+'%</strong> on the Infinity Loop &mdash; up 2.3% from last survey, trending toward Professional Grade across 11 missions.';
    if(typeof buildScoreLabels==='function'){try{buildScoreLabels();}catch(e){}}
  }
})();
/* route the Hardware→Drone entry to the new DATUM renderer */
buildDronePage = function(){
  if(window.dsDrone){
    if(window.dsDrone.refreshApi) window.dsDrone.refreshApi();
    window.dsDrone.render();
  }
};


/* ═══════════════════════════════════════════════
   GCP (DATUM hero) — locked per-point chain + UI, namespaced via window.dsGcp
   Data+engine ported verbatim from gcp_multi_view_v1_LOCKED.html (284-670)
   ═══════════════════════════════════════════════ */
(function(){
const BLOCKS = [
  { id: "BB_GCP_COMPLETE", name: "Capture Completeness & Integrity", weight: 0.45,
    description: "Whether per-point captures are present, complete, and usable." },
  { id: "BB_GCP_SETUP", name: "Per-point Setup & Documentation Confidence", weight: 0.35,
    description: "Whether per-point operator metadata (antenna height, device ID, antenna type) is trustworthy." },
  { id: "BB_GCP_ENV", name: "Per-point Observation Environment", weight: 0.20,
    description: "Whether sky/site environment was good at each point (multipath, acquisition, ionospheric)." },
];

const GLOBAL_GATE_CONDITION = "every_designated_gcp.occupation_coverage_score == 0 (no usable Control Point captures anywhere)";

// ============================================================
// INDICATOR LIBRARY (loaded inline; would normally be from JSON file)
// ============================================================
const INDICATOR_LIBRARY = {
  "L3I_GCP_001": {
    id: "L3I_GCP_001", num: "#01", block: "BB_GCP_COMPLETE", weight: 0.35,
    name: "Coverage", fullName: "occupation_coverage_score",
    is_critical_path: true, gate_scope: "chain_level_when_all_points",
    verified_statement: "Control Point {point_id} was occupied for the full required duration with adequate buffer time.",
    bands: [
      { score_range: [88, 100], level: "good", label: "Coverage 100% + pre-occupation buffer ≥120s + post-occupation buffer ≥60s", impact: null, actions: null },
      { score_range: [72, 87], level: "good", label: "Coverage 100%, pre-occupation buffer 60-120s", impact: null, actions: null },
      { score_range: [40, 71], level: "review", label: "Coverage 100% but pre-occupation buffer <60s",
        impact: "Control Point {point_id} convergence may be incomplete at occupation start. Early epochs can carry residual error.",
        actions: ["Allow 2 min for the device to converge before walking away from the point next time","Review processed sigma on early epochs at this point","If accuracy is critical, consider trimming noisy early data"] },
      { score_range: [0, 0], level: "resurvey", label: "Control Point {point_id} has no usable occupation data (per-point coverage failed)",
        impact: "Control Point {point_id} cannot contribute to control. If multiple points fail, chain-level hard gate fires.",
        actions: ["Re-occupy this specific point","Allow full occupation duration with proper buffer","Verify recording started before occupation and stopped after"] }
    ],
    derivation: "Per-point coverage gate (score=0) degrades that point's contribution but does NOT fire chain-level hard gate. Chain-level hard gate fires only when EVERY designated Control Point has score=0 (Q-CP-1 lock).",
    flag: "GCP_POINT_FLIGHT_GAP"
  },
  "L3I_GCP_002": {
    id: "L3I_GCP_002", num: "#02", block: "BB_GCP_COMPLETE", weight: 0.30,
    name: "Integrity", fullName: "occupation_integrity_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Control Point {point_id} occupation completed normally with no interruptions and battery state healthy.",
    bands: [
      { score_range: [85, 100], level: "good", label: "Clean session, no shutdowns, battery ≥20%", impact: null, actions: null },
      { score_range: [40, 84], level: "review", label: "Operation log absent — occupation integrity unconfirmed",
        impact: "Cannot verify Control Point {point_id} occupation completed cleanly. Data may still be usable but audit trail is incomplete.",
        actions: ["Verify recorded file size matches expected occupation duration","Inspect last epoch timestamp against planned end","Ensure operation log uploads with device files next time"] },
      { score_range: [0, 39], level: "resurvey", label: "Device shutdown during occupation at point {point_id}",
        impact: "Device shut down unexpectedly during occupation at Control Point {point_id}. Recording may be truncated or corrupted.",
        actions: ["Inspect device file around shutdown timestamp","If shutdown occurred outside occupation window, data may still process","If shutdown occurred during occupation, re-occupy this point"] }
    ],
    derivation: "Score 60 (operation log absent) conservative middle band. Device-type conditional (CB_X/AEROPOINT don't expect oplog).",
    flag: "GCP_POINT_DEVICE_FAILURE"
  },
  "L3I_GCP_003": {
    id: "L3I_GCP_003", num: "#03", block: "BB_GCP_COMPLETE", weight: 0.20,
    name: "Format", fullName: "occupation_format_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Control Point {point_id} data is in supported format with complete header and dual-frequency observations.",
    bands: [
      { score_range: [85, 100], level: "good", label: "Supported version, complete header, dual-frequency", impact: null, actions: null },
      { score_range: [40, 84], level: "review", label: "Single-frequency or incomplete header at point {point_id}",
        impact: "Single-frequency observations at Control Point {point_id} mean ionospheric error cannot be modeled. Accuracy degrades during active solar weather.",
        actions: ["Verify device was configured for dual-frequency at this point","Patch missing header fields if known","Expect reduced accuracy at this point under high Kp index"] },
      { score_range: [0, 39], level: "review", label: "File version not supported by PPK software (point {point_id})",
        impact: "Device file from Control Point {point_id} cannot be ingested directly. Data is fine — format conversion required.",
        actions: ["Convert file to a supported version using vendor or standard converter","Verify converted file passes format validation","Resubmit for processing"] }
    ],
    derivation: "Following base_station v2.1 Q2 lock pattern: version-unsupported is review (material), not resurvey. Data is fine; format conversion is fixable without recollection.",
    flag: "GCP_POINT_RINEX_VERSION_UNSUPPORTED"
  },
  "L3I_GCP_004": {
    id: "L3I_GCP_004", num: "#04", block: "BB_GCP_COMPLETE", weight: 0.15,
    name: "Continuity", fullName: "occupation_continuity_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Continuous observations at Control Point {point_id} with minimal cycle slips.",
    bands: [
      { score_range: [75, 100], level: "good", label: "No gaps or minor gaps only (<60s), minimal cycle slips", impact: null, actions: null },
      { score_range: [40, 74], level: "review", label: "Gap >60s detected at point {point_id} — PPK must re-converge",
        impact: "Control Point {point_id} lost satellite tracking for >60s. PPK must re-converge after the gap, reducing accuracy in that window.",
        actions: ["Check whether occupation was interrupted (operator walked away?)","Investigate cause — signal blockage, brief power loss, person standing on point?","Position next occupation in clearer location"] },
      { score_range: [0, 39], level: "review", label: "Multiple disturbances or extensive gaps at point {point_id}",
        impact: "Significant disturbance pattern at Control Point {point_id}. Position oscillations likely in processed output.",
        actions: ["Inspect raw file for repeated signal loss events","Consider re-occupying this point in a less disturbed environment","Flag for review during processing residual analysis"] }
    ],
    derivation: "60s gap threshold from industry PPK best practice. Feeds disturbance composite.",
    flag: "GCP_POINT_DISTURBANCE"
  },
  "L3I_GCP_005": {
    id: "L3I_GCP_005", num: "#05", block: "BB_GCP_SETUP", weight: 0.55,
    name: "Antenna height", fullName: "gcp_antenna_height_documented_score",
    is_critical_path: false, gate_scope: "per_point_only",
    verified_statement: "Antenna height at Control Point {point_id} documented to ARP, matching device file delta-H.",
    bands: [
      { score_range: [100, 100], level: "good", label: "Factory-known antenna height (CB_X / AEROPOINT — auto-100)", impact: null, actions: null },
      { score_range: [85, 99], level: "good", label: "DGPS device — vertical measurement to ARP, ≥3 measurements, matches RINEX", impact: null, actions: null },
      { score_range: [40, 84], level: "review", label: "Slant measurement, single measurement, or RINEX delta-H disagreement at point {point_id}",
        impact: "Antenna height measurement at Control Point {point_id} carries higher uncertainty. Wrong height shifts elevation reference for that point.",
        actions: ["Verify slant-to-vertical conversion was applied at point {point_id}","Reconcile entered value against RINEX delta-H for this point","Re-measure vertical-to-ARP if uncertain"] },
      { score_range: [0, 0], level: "resurvey", label: "Antenna height missing at Control Point {point_id} (PER-POINT GATE)",
        impact: "Control Point {point_id} elevation reference is unknown. This point cannot contribute to elevation control. NOT a chain-level hard gate — other points still score.",
        actions: ["Enter antenna height for point {point_id} now if recoverable from field notes","If not recoverable, re-occupy this specific point with measured height","Measure 3 times vertical-to-ARP, average for best confidence"] }
    ],
    derivation: "PER-POINT GATE only (Q-CP-1 lock). Missing antenna height on one Control Point degrades that point's contribution but does NOT fire chain-level hard gate. CB_X/AEROPOINT auto-score 100 (factory-known).",
    flag: "GCP_POINT_ANTENNA_HEIGHT_MISSING"
  },
  "L3I_GCP_006": {
    id: "L3I_GCP_006", num: "#06", block: "BB_GCP_SETUP", weight: 0.30,
    name: "Device ID match", fullName: "gcp_device_id_match_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Device ID at Control Point {point_id} matches between operator form and RINEX header.",
    bands: [
      { score_range: [85, 100], level: "good", label: "Form device ID matches RINEX header", impact: null, actions: null },
      { score_range: [55, 84], level: "review", label: "Device ID unconfirmed at point {point_id} (one side missing)",
        impact: "Cannot verify which physical device captured Control Point {point_id} data. Audit trail incomplete.",
        actions: ["Check field notes for device serial used at this point","Update either form or header with correct device ID","Document for QA"] },
      { score_range: [0, 54], level: "review", label: "Device ID mismatch at point {point_id} (form vs file)",
        impact: "Form-declared device differs from device that wrote the file at Control Point {point_id}. Wrong device profile may produce systematic positioning bias.",
        actions: ["Verify physical device used at this point — check field photos if available","Update whichever source is incorrect","Re-process with correct device profile"] }
    ],
    derivation: "Reviewer-blocking flag — significant audit trail concern. Mismatch indicates form was filled incorrectly or wrong file was uploaded for this point.",
    flag: "GCP_POINT_DEVICE_ID_MISMATCH"
  },
  "L3I_GCP_007": {
    id: "L3I_GCP_007", num: "#07", block: "BB_GCP_SETUP", weight: 0.15,
    name: "Antenna type match", fullName: "gcp_antenna_type_match_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Antenna type at Control Point {point_id} matches between form and RINEX header.",
    bands: [
      { score_range: [85, 100], level: "good", label: "Form antenna matches RINEX header", impact: null, actions: null },
      { score_range: [0, 84], level: "review", label: "Antenna type mismatch at point {point_id}",
        impact: "Wrong antenna profile at Control Point {point_id} means wrong ANTEX calibration — systematic position bias from millimeters to centimeters at this point.",
        actions: ["Verify physical antenna at point {point_id} against form selection","Update whichever source is incorrect","Re-process this point with correct ANTEX profile"] }
    ],
    derivation: "Type-string consistency check only — not a true ANTEX phase-center calibration (Stage-2 concern in pre_processing chain).",
    flag: null
  },
  "L3I_GCP_008": {
    id: "L3I_GCP_008", num: "#08", block: "BB_GCP_ENV", weight: 0.50,
    name: "Multipath", fullName: "gcp_multipath_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Clean signal environment at Control Point {point_id} — low C/N0 variance.",
    bands: [
      { score_range: [75, 100], level: "good", label: "Low C/N0 variance (<2.5 dB-Hz) — clean signal at point {point_id}", impact: null, actions: null },
      { score_range: [40, 74], level: "review", label: "Moderate multipath risk at point {point_id}",
        impact: "Control Point {point_id} was near reflective surfaces. PPK can usually handle it but residuals may show position oscillations at this point.",
        actions: ["Check processed residuals for oscillations at point {point_id}","Position next occupation ≥10m from buildings/vehicles/water","Choose open-sky locations for high-stakes work"] },
      { score_range: [0, 39], level: "review", label: "High multipath risk at point {point_id} (>4.0 dB-Hz variance)",
        impact: "Control Point {point_id} was clearly in a reflective environment. PPK output for this point likely has elevated residuals.",
        actions: ["Inspect processed position output for this point","Consider re-occupying at cleaner site if accuracy is critical","Scout point locations for reflective surfaces in future surveys"] }
    ],
    derivation: "C/N0 variance as multipath proxy. Threshold values heuristic — calibration against measured PPK residuals is a future deliverable.",
    flag: "GCP_POINT_HIGH_MULTIPATH"
  },
  "L3I_GCP_009": {
    id: "L3I_GCP_009", num: "#09", block: "BB_GCP_ENV", weight: 0.30,
    name: "Acquisition", fullName: "gcp_acquisition_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Device at Control Point {point_id} acquired satellites within normal startup time.",
    bands: [
      { score_range: [75, 100], level: "good", label: "<2 min — normal startup at point {point_id}", impact: null, actions: null },
      { score_range: [0, 74], level: "minor", label: ">2 min — slow startup at point {point_id} (hygiene signal)",
        impact: "Device was slow to lock onto satellites at Control Point {point_id}. Usually cold start. Recurring slowness can indicate device health issues, but rarely affects this point's data quality.",
        actions: ["Check device location for obstructions at this point","Verify device firmware and battery state","Service unit if slow acquisition is recurring across multiple occupations"] }
    ],
    derivation: "Following base_station Q4 lock pattern: acquisition slowness is hygiene signal, not deliverable-quality concern. Downgraded to minor (audit-only).",
    flag: "GCP_POINT_SLOW_ACQUISITION"
  },
  "L3I_GCP_010": {
    id: "L3I_GCP_010", num: "#10", block: "BB_GCP_ENV", weight: 0.20,
    name: "Ionospheric risk", fullName: "gcp_ionospheric_risk_score",
    is_critical_path: false, gate_scope: "none",
    verified_statement: "Either calm geomagnetic conditions or dual-frequency observations at Control Point {point_id}.",
    bands: [
      { score_range: [85, 100], level: "good", label: "Low Kp (≤4) OR dual-frequency device — ionospheric error modeled", impact: null, actions: null },
      { score_range: [50, 84], level: "review", label: "Moderate Kp (5-6) and single-frequency at point {point_id}",
        impact: "Active solar weather + single-frequency device at Control Point {point_id} means ionospheric delay cannot be modeled fully.",
        actions: ["Check NOAA SWPC Kp index for occupation window","Prefer dual-frequency devices for future surveys","Document for QA — expected accuracy reduced at this point"] },
      { score_range: [0, 49], level: "review", label: "Severe geomagnetic storm (Kp ≥7) and single-frequency at point {point_id}",
        impact: "Severe geomagnetic conditions at Control Point {point_id} during occupation. Position errors can reach decimeters under these conditions with single-frequency receivers.",
        actions: ["Re-occupy this point during calmer space weather if accuracy is critical","Replace single-frequency devices with dual-frequency for future surveys","Reschedule surveys when forecast Kp ≥5 and accuracy is critical"] }
    ],
    derivation: "Kp ≥5 threshold from NOAA space weather scale (G1+ geomagnetic storm). External NOAA SWPC dependency.",
    flag: "GCP_POINT_IONO_RISK"
  }
};
const INDICATORS = Object.values(INDICATOR_LIBRARY);

// ============================================================
// SCENARIOS — per-point structure
// Each scenario has multiple points with their per-indicator scores
// ============================================================
const SCENARIOS = [
  {
    id: "clean", name: "Clean Survey (8 Control Points)",
    desc: "All 8 Control Points occupied cleanly with verified setup",
    points: [
      { id: "CP-001", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-002", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-003", device_type: "CB_X", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:100, L3I_GCP_010:100 } },
      { id: "CP-004", device_type: "CB_X", scores: { L3I_GCP_001:88, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-005", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:88, L3I_GCP_010:100 } },
      { id: "CP-006", device_type: "AEROPOINT", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:95, L3I_GCP_010:100 } },
      { id: "CP-007", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:95, L3I_GCP_009:88, L3I_GCP_010:100 } },
      { id: "CP-008", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } }
    ]
  },
  {
    id: "review", name: "Mixed Quality (per-point issues)",
    desc: "8 Control Points total — CP-003 antenna height conflict, CP-005 type mismatch, CP-007 high multipath",
    points: [
      { id: "CP-001", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-002", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-003", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:55, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-004", device_type: "CB_X", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-005", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:40, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-006", device_type: "AEROPOINT", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:95, L3I_GCP_010:100 } },
      { id: "CP-007", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:35, L3I_GCP_009:88, L3I_GCP_010:100 } },
      { id: "CP-008", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } }
    ]
  },
  {
    id: "per_point_resurvey", name: "Per-Point Critical (one point unrecoverable)",
    desc: "8 Control Points — CP-003 has missing antenna height (per-point gate); chain still scores from other 7 points",
    points: [
      { id: "CP-001", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-002", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-003", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:0,  L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-004", device_type: "CB_X", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-005", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:90, L3I_GCP_010:100 } },
      { id: "CP-006", device_type: "AEROPOINT", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:100, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:100, L3I_GCP_009:95, L3I_GCP_010:100 } },
      { id: "CP-007", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:95, L3I_GCP_009:88, L3I_GCP_010:100 } },
      { id: "CP-008", device_type: "DGPS", scores: { L3I_GCP_001:100, L3I_GCP_002:100, L3I_GCP_003:100, L3I_GCP_004:100, L3I_GCP_005:95, L3I_GCP_006:100, L3I_GCP_007:100, L3I_GCP_008:90, L3I_GCP_009:90, L3I_GCP_010:100 } }
    ]
  },
  {
    id: "no_gcps", name: "NO_DESIGNATED_GCPS (chain not applicable)",
    desc: "Survey designed without Control Points — chain returns null (Q-CP-2)",
    points: [],
    no_gcps: true
  }
];

// ============================================================
// HELPERS
// ============================================================
function getBandForScore(indicator, score) {
  for (const band of indicator.bands) {
    const [lo, hi] = band.score_range;
    if (score >= lo && score <= hi) return band;
  }
  return indicator.bands[indicator.bands.length - 1];
}

function severityForBand(band) {
  if (band.level === "resurvey") return "critical";
  if (band.level === "review")   return "material";
  if (band.level === "minor")    return "minor";
  return "none";
}

function severityForScore(indicator, score) {
  return severityForBand(getBandForScore(indicator, score));
}

function scoreLevel(score) {
  if (score === 0) return "resurvey";
  if (score >= 75) return "good";
  if (score >= 50) return "review";
  return "resurvey";
}

function substitutePointId(text, pointId) {
  if (!text) return text;
  return text.replace(/\{point_id\}/g, pointId);
}

// ============================================================
// SCORING — per-point chain
// ============================================================
function computePointBlockScore(blockId, pointScores) {
  const inds = INDICATORS.filter(i => i.block === blockId);
  let totalW = 0, sumW = 0;
  for (const i of inds) {
    const s = pointScores[i.id];
    if (s === undefined) continue;
    totalW += i.weight;
    sumW += i.weight * s;
  }
  return totalW > 0 ? sumW / totalW : 0;
}

function computePointScore(point) {
  // Per-point overall score (weighted by blocks)
  // If antenna height = 0 (per-point gate), that point's setup block goes to ~0
  let totalW = 0, sumW = 0;
  for (const b of BLOCKS) {
    const bs = computePointBlockScore(b.id, point.scores);
    totalW += b.weight;
    sumW += b.weight * bs;
  }
  return totalW > 0 ? sumW / totalW : 0;
}

function checkChainHardGate(points) {
  // Chain-level hard gate: ALL points have L3I_GCP_001 = 0
  if (points.length === 0) return { fired: false };
  const allFailed = points.every(p => p.scores["L3I_GCP_001"] === 0);
  return { fired: allFailed };
}

function computeChainScore(scenario) {
  if (scenario.no_gcps) {
    return { score: null, status: "NOT_APPLICABLE", hardGate: false };
  }
  const gate = checkChainHardGate(scenario.points);
  if (gate.fired) {
    return { score: 0, status: "HARD_GATE_FIRED", hardGate: true };
  }
  // Average per-point scores
  const pointScores = scenario.points.map(computePointScore);
  const avg = pointScores.reduce((a,b) => a+b, 0) / pointScores.length;
  return { score: avg, status: "NORMAL", hardGate: false };
}

function overallRecommendation(scenario) {
  const overall = computeChainScore(scenario);
  if (overall.status === "NOT_APPLICABLE") return { rec: "na", overall };
  if (overall.hardGate) return { rec: "resurvey", overall };
  // Check if any point has critical or material findings
  let hasCritical = false, hasMaterial = false;
  for (const p of scenario.points) {
    for (const i of INDICATORS) {
      const sev = severityForScore(i, p.scores[i.id]);
      if (sev === "critical") hasCritical = true;
      else if (sev === "material") hasMaterial = true;
    }
  }
  if (hasCritical) return { rec: "resurvey", overall };
  if (hasMaterial) return { rec: "review", overall };
  return { rec: "good", overall };
}

// ============================================================
// RANKING — per-point findings aggregated
// ============================================================
function rankFindings(scenario) {
  const findings = [];
  for (const p of scenario.points) {
    for (const i of INDICATORS) {
      const s = p.scores[i.id];
      const band = getBandForScore(i, s);
      if (band.level === "good") continue;
      if (band.level === "minor") continue;
      const blockWeight = BLOCKS.find(b => b.id === i.block).weight;
      const deficit = 100 - s;
      const isHardGate = (band.score_range[0] === 0 && band.score_range[1] === 0 && (i.is_critical_path || i.gate_scope === "per_point_only"));
      const isCritical = (band.level === "resurvey");
      findings.push({
        indicator: i, score: s, band, point: p,
        sev: severityForScore(i, s),
        isHardGate, isCritical,
        priority: isHardGate ? 1000 : (isCritical ? 500 + blockWeight * deficit : blockWeight * deficit)
      });
    }
  }
  findings.sort((a, b) => b.priority - a.priority);
  return findings;
}

function rankVerified(scenario) {
  // Find indicators that scored good across all points — for "verified hero"
  // Strategy: rank by critical-path indicators first, then by block_weight × indicator_weight
  // Aggregate: an indicator counts as "verified" if ALL points pass it
  const candidates = [];
  for (const i of INDICATORS) {
    const allPass = scenario.points.every(p => {
      const band = getBandForScore(i, p.scores[i.id]);
      return band.level === "good";
    });
    if (allPass) {
      const blockWeight = BLOCKS.find(b => b.id === i.block).weight;
      candidates.push({
        indicator: i,
        priority: (i.is_critical_path ? 1000 : 0) + blockWeight * i.weight * 100
      });
    }
  }
  candidates.sort((a, b) => b.priority - a.priority);
  return candidates;
}


/* ===========================================================================
   Control Point UI LAYER — per-point chain, rendered into the DATUM hero structure.
   Assumes the LOCKED gcp data+engine is defined above:
     BLOCKS, INDICATOR_LIBRARY, INDICATORS, SCENARIOS, GLOBAL_GATE_CONDITION,
     getBandForScore, severityForBand, severityForScore, scoreLevel, substitutePointId,
     computePointBlockScore, computePointScore, checkChainHardGate, computeChainScore,
     overallRecommendation(scenario), rankFindings(scenario), rankVerified(scenario),
     recommendationLabel(rec)
   Control Point is a PER-POINT chain: each scenario has points[], each with its own scores.
   The hero aggregates across points; the drawers list point-specific findings.
   All DOM ids are prefixed "gp-" (legacy gc- ids are replaced).
   =========================================================================== */

var REC_LABEL={good:"GOOD TO GO",review:"REVIEW",resurvey:"RESURVEY",na:"NOT APPLICABLE"};
var REC_VERDICT_COLOR={good:"rgba(16,185,214,.9)",review:"rgba(232,228,218,.94)",resurvey:"var(--red)",na:"rgba(232,228,218,.6)"};
var REC_REASON={
  good:"All Control Point occupations passed across every point — control is survey-grade.",
  review:"One or more points carry soft flags — review them before relying on the control.",
  resurvey:"A point has a critical occupation failure — re-occupy the flagged point(s).",
  na:"This survey was designed without Control Points (PPK / PPP workflow) — the Control Point confidence chain doesn't apply."
};
var SCEN_SHORT={clean:"Clean",review:"Mixed",per_point_resurvey:"Per-Point",no_gcps:"No Control Points"};
var LVL_RANK={good:0,minor:1,review:2,resurvey:3};

function blockIndicators(blockId){return INDICATORS.filter(function(i){return i.block===blockId;});}
function pctRound(n){return Math.round(n);}
function statusText(level){return level==="good"?"OK":(level==="resurvey"?"Resurvey":(level==="na"?"N/A":"Review"));}
function hasPoints(s){return !!(s&&s.points&&s.points.length);}

/* canonical aggregation: per block, mean − 0.25×(100 − min) across points, then block-weighted */
function blockPerPoint(blockId,scenario){return scenario.points.map(function(p){return computePointBlockScore(blockId,p.scores);});}
function aggBlockScore(blockId,scenario){
  if(!hasPoints(scenario))return null;
  var ps=blockPerPoint(blockId,scenario);
  var mean=ps.reduce(function(a,c){return a+c;},0)/ps.length;
  var min=Math.min.apply(null,ps);
  return mean-0.25*(100-min);
}
function aggOverallCanon(scenario){
  if(!hasPoints(scenario))return null;
  if(checkChainHardGate(scenario.points).fired)return 0;
  var s=0;BLOCKS.forEach(function(b){s+=b.weight*aggBlockScore(b.id,scenario);});
  return s;
}
/* display name — drop the "Per-point" prefix for clean, consistent block names */
function blockName(b){return (b.name||"").replace(/^Per-point\s+/i,"");}
function aggIndicatorLevel(ind,scenario){
  if(!hasPoints(scenario))return "good";
  var worst="good";
  scenario.points.forEach(function(p){
    var lvl=getBandForScore(ind,p.scores[ind.id]).level;
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst;
}
function aggIndicatorWorstScore(ind,scenario){
  if(!hasPoints(scenario))return null;
  var mn=101;scenario.points.forEach(function(p){var s=p.scores[ind.id];if(s<mn)mn=s;});
  return mn===101?null:mn;
}
function worstPointFor(ind,scenario){
  var wp=null,mn=101;
  (scenario.points||[]).forEach(function(p){var s=p.scores[ind.id];if(s<mn){mn=s;wp=p;}});
  return wp;
}
function aggBlockLevel(blockId,scenario){
  if(!hasPoints(scenario))return "na";
  var worst="good";
  blockIndicators(blockId).forEach(function(ind){
    var lvl=aggIndicatorLevel(ind,scenario);
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst;
}
function importance(ind){
  var b=BLOCKS.filter(function(x){return x.id===ind.block;})[0];var bw=b?b.weight:0;
  return (ind.is_critical_path?1000:0)+bw*ind.weight*100;
}
/* rankFindings excludes minor — gather minor (hygiene) findings separately for "Noted" */
function minorFindings(scenario){
  var out=[];
  (scenario.points||[]).forEach(function(p){
    INDICATORS.forEach(function(i){
      var band=getBandForScore(i,p.scores[i.id]);
      if(band.level==="minor")out.push({indicator:i,point:p,band:band,score:p.scores[i.id]});
    });
  });
  return out;
}
function scenRec(s){return overallRecommendation(s).rec;}

/* per-point overall severity (for the roster chips) */
function pointLevel(p){
  var worst="good";
  INDICATORS.forEach(function(i){
    var lvl=getBandForScore(i,p.scores[i.id]).level;
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst==="minor"?"good":worst;
}
function renderRoster(){
  var host=document.getElementById("gp-roster"),sum=document.getElementById("gp-rosterSummary");
  if(!host)return;
  var sc=currentScenario;
  if(!hasPoints(sc)){
    host.innerHTML='<div class="gp-roster-empty">No Control Points were designated for this survey — control comes from the PPK / PPP workflow.</div>';
    if(sum)sum.textContent="0 points";return;
  }
  var ok=0,flag=0;
  host.innerHTML=sc.points.map(function(p){
    var lvl=pointLevel(p); if(lvl==="good")ok++;else flag++;
    var ps=pctRound(computePointScore(p));
    var on=currentPoint===p.id?" selected":"";
    return '<div class="gp-pt '+lvl+on+'" onclick="dsGcp.selectPoint(\''+p.id+'\')" title="'+p.id+' · '+statusText(lvl)+' · '+ps+'%">'+
        '<div class="gp-pt-top"><span class="gp-pt-dot"></span><span class="gp-pt-id">'+p.id+'</span></div>'+
        '<div class="gp-pt-dev">'+(p.device_type||"")+' · '+ps+'%</div>'+
      '</div>';
  }).join("");
  if(sum)sum.textContent=sc.points.length+" Control Points · "+ok+" OK · "+flag+" flagged";
}

/* illustrative trend (sample); last point ≈ the real Mixed-Quality score */
var TREND=[
  {sid:"S-061",date:"Oct 25",score:93,anom:false},
  {sid:"S-062",date:"Nov 25",score:95,anom:false},
  {sid:"S-063",date:"Nov 25",score:88,anom:true,note:"Two points high multipath"},
  {sid:"S-064",date:"Dec 25",score:94,anom:false},
  {sid:"S-065",date:"Dec 25",score:97,anom:false},
  {sid:"S-066",date:"Jan 26",score:96,anom:false},
  {sid:"S-067",date:"Jan 26",score:95,anom:false},
  {sid:"S-068",date:"Feb 26",score:97,anom:false},
  {sid:"S-069",date:"Mar 26",score:98,anom:false},
  {sid:"S-070",date:"May 26",score:96,anom:false}
];
var FLEET=[90,92,89,93,95,94,93,95,96,95];
var fleetOn=false;

var POS={
  1:[[50,16]],
  2:[[26,30],[74,30]],
  3:[[24,40],[76,40],[50,84]],
  4:[[22,32],[78,32],[28,76],[72,76]],
  5:[[50,12],[20,34],[80,34],[30,82],[70,82]],
  6:[[22,20],[78,20],[14,52],[86,52],[34,84],[66,84]],
  7:[[50,10],[20,26],[80,26],[12,56],[88,56],[34,86],[66,86]]
};
function posFor(n){
  if(POS[n])return POS[n];
  var a=[];for(var i=0;i<n;i++){var ang=(i/n)*2*Math.PI-Math.PI/2;a.push([50+38*Math.cos(ang),50+40*Math.sin(ang)]);}
  return a;
}

var currentScenario=SCENARIOS.filter(function(s){return s.id==="review";})[0]||SCENARIOS[0];
var currentPoint=null;   /* null = Overall (aggregate); else a point id = single-point view */
var selected={};

/* point-scoped recommendation copy (single-point view) */
var POINT_REASON={
  good:"This Control Point passed every check — survey-grade control at this point.",
  review:"This Control Point has soft flags — review them before relying on it.",
  resurvey:"This Control Point has a critical occupation failure — re-occupy this point."
};
function curPoint(){return currentPoint?((currentScenario.points||[]).filter(function(p){return p.id===currentPoint;})[0]||null):null;}
function pointRec(p){
  var worst="good";
  INDICATORS.forEach(function(ind){
    var sev=severityForScore(ind,p.scores[ind.id]);
    if(sev==="critical")worst="resurvey";
    else if(sev==="material"&&worst!=="resurvey")worst="review";
  });
  return {rec:worst,score:computePointScore(p)};
}
function pointBlockLevel(blockId,p){
  var worst="good";
  blockIndicators(blockId).forEach(function(ind){
    var lvl=getBandForScore(ind,p.scores[ind.id]).level;
    if(lvl==="resurvey")worst="resurvey";
    else if(lvl==="review"&&worst!=="resurvey")worst="review";
  });
  return worst;
}
function renderPointSelect(){
  var el=document.getElementById("gp-pointSelect");if(!el)return;
  if(!hasPoints(currentScenario)){el.innerHTML='<option value="overall" selected>Overall</option>';el.disabled=true;return;}
  el.disabled=false;
  var o='<option value="overall"'+(currentPoint?'':' selected')+'>Overall</option>';
  currentScenario.points.forEach(function(p){
    o+='<option value="'+p.id+'"'+(currentPoint===p.id?' selected':'')+'>'+p.id+(p.device_type?' · '+p.device_type:'')+'</option>';
  });
  el.innerHTML=o;
}
function selectPoint(val){currentPoint=(val&&val!=="overall")?val:null;selected={};closeDrawer();renderAll();}

/* ---- sparkline ---- */
(function(){
  var svg=document.getElementById("gp-sparkSvg");if(!svg)return;
  var W=192,H=52,pL=2,pR=2,pT=5,pB=5,n=TREND.length,mn=75,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*(W-pL-pR):(W-pL-pR)/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*(H-pT-pB)};
  var area="M "+pL+" "+(H-pB);TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});area+=" L "+(W-pR)+" "+(H-pB)+" Z";
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  var s='<path fill="url(#gp-spGrad)" d="'+area+'"/><path fill="none" stroke="rgba(16,185,214,.5)" stroke-width="1" d="'+line+'"/>';
  TREND.forEach(function(d,i){s+='<circle fill="'+(d.anom?"rgba(232,228,218,.4)":"rgba(16,185,214,.5)")+'" cx="'+sx(i)+'" cy="'+sy(d.score)+'" r="1.8"><title>'+d.sid+" · "+d.score+'</title></circle>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1" cx="'+lx+'" cy="'+ly+'" r="3.8"/>';
  svg.innerHTML+=s;
})();
function openTrend(){document.getElementById("gp-trendModal").classList.add("open");drawTrend();}
function closeTrend(){document.getElementById("gp-trendModal").classList.remove("open");}
function toggleFleet(){fleetOn=!fleetOn;document.getElementById("gp-fleetBtn").classList.toggle("on",fleetOn);drawTrend();}
function drawTrend(){
  var svg=document.getElementById("gp-trendSvg");var W=900,H=256,pL=44,pR=24,pT=14,pB=34,iW=W-pL-pR,iH=H-pT-pB,n=TREND.length,mn=40,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*iW:iW/2)};var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*iH};
  var s='<defs><linearGradient id="gp-tgGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(16,185,214,.25)"/><stop offset="100%" stop-color="rgba(16,185,214,.00)"/></linearGradient></defs>';
  [40,60,75,90,100].forEach(function(v){s+='<line class="tg-axis" x1="'+pL+'" y1="'+sy(v)+'" x2="'+(W-pR)+'" y2="'+sy(v)+'"/><text class="tg-tick" x="'+(pL-6)+'" y="'+(sy(v)+3)+'" text-anchor="end">'+v+'</text>';});
  s+='<rect class="tg-band" x="'+pL+'" y="'+sy(100)+'" width="'+iW+'" height="'+(sy(90)-sy(100))+'"/>';
  var area="M "+pL+" "+sy(mn);TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});area+=" L "+(W-pR)+" "+sy(mn)+" Z";
  s+='<path d="'+area+'" fill="url(#gp-tgGrad)"/>';
  if(fleetOn){var fp="";FLEET.forEach(function(v,i){fp+=(i===0?"M ":"L ")+sx(i)+" "+sy(v)+" "});s+='<path class="tg-fleet" d="'+fp+'"/><text class="tg-lbl" x="'+(sx(FLEET.length-1)+5)+'" y="'+(sy(FLEET[FLEET.length-1])+3)+'">fleet median</text>';}
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});s+='<path class="tg-line" d="'+line+'"/>';
  TREND.forEach(function(d,i){var x=sx(i),y=sy(d.score);s+='<circle class="tg-pt'+(d.anom?" anom":"")+'" cx="'+x+'" cy="'+y+'" r="4.5"><title>'+d.sid+" · "+d.score+(d.note?" ("+d.note+")":"")+'</title></circle>';if(i%2===0||i===n-1)s+='<text class="tg-tick" x="'+x+'" y="'+(H-pB+13)+'" text-anchor="middle">'+d.date+'</text>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1"/><text class="tg-lbl" x="'+(lx-7)+'" y="'+(ly-11)+'" text-anchor="end" fill="rgba(16,185,214,.6)">current · '+TREND[n-1].score+'</text>';
  svg.innerHTML=s;
}
function toggleBBSection(){document.getElementById("gp-bbSectionBody").classList.toggle("open");document.getElementById("gp-bbSectionIcon").classList.toggle("open");}

/* ---- picker ---- */
function renderScenarioPicker(){
  var el=document.getElementById("gp-scnPick");if(!el)return;
  el.innerHTML=SCENARIOS.map(function(s){
    var on=s.id===currentScenario.id,rec=scenRec(s);
    var cls="scn-opt"+(on?" on":"");
    if(on&&(rec==="review"||rec==="na"))cls+=" warn";
    if(on&&rec==="resurvey")cls+=" bad";
    return '<button class="'+cls+'" onclick="dsGcp.selectScenario(\''+s.id+'\')">'+(SCEN_SHORT[s.id]||s.name)+'</button>';
  }).join("");
}
function selectScenario(id){var s=SCENARIOS.filter(function(x){return x.id===id;})[0];if(!s)return;currentScenario=s;currentPoint=null;selected={};closeDrawer();renderAll();}

/* ---- headline ---- */
function renderHeadline(){
  var p=curPoint();
  if(p){
    var pr=pointRec(p);
    document.getElementById("gp-scoreNum").innerHTML=pctRound(pr.score)+'<span class="pct">%</span>';
    document.getElementById("gp-scoreDelta").textContent=p.id+(p.device_type?" · "+p.device_type:"")+" · single point";
    var vt0=document.getElementById("gp-mdVerdictText");if(vt0)vt0.textContent=REC_LABEL[pr.rec];
    var v0=document.getElementById("gp-mdVerdict");if(v0)v0.style.color=REC_VERDICT_COLOR[pr.rec];
    document.getElementById("gp-mdReason").innerHTML=POINT_REASON[pr.rec];
    return;
  }
  var rec=overallRecommendation(currentScenario),ov=rec.overall;
  var numEl=document.getElementById("gp-scoreNum"),dEl=document.getElementById("gp-scoreDelta");
  if(ov.status==="NOT_APPLICABLE"){
    numEl.innerHTML='N/A';
    dEl.textContent="Survey designed without Control Points";
  }else if(ov.hardGate){
    numEl.innerHTML='0<span class="pct">%</span>';
    dEl.textContent="Chain hard gate — no usable Control Point captures";
  }else{
    numEl.innerHTML=pctRound(aggOverallCanon(currentScenario))+'<span class="pct">%</span>';
    dEl.textContent="Across "+currentScenario.points.length+" Control Points · "+BLOCKS.length+" blocks";
  }
  var vt=document.getElementById("gp-mdVerdictText");if(vt)vt.textContent=REC_LABEL[rec.rec];
  var verdict=document.getElementById("gp-mdVerdict");if(verdict)verdict.style.color=REC_VERDICT_COLOR[rec.rec];
  document.getElementById("gp-mdReason").innerHTML=REC_REASON[rec.rec];
}

/* ---- BB cards ---- */
function renderBBCards(){
  var sc=currentScenario,p=curPoint();
  var host=document.getElementById("gp-bbStripHead");
  host.innerHTML=BLOCKS.map(function(b,idx){
    var raw,lvl;
    if(p){ raw=computePointBlockScore(b.id,p.scores); lvl=pointBlockLevel(b.id,p); }
    else { raw=aggBlockScore(b.id,sc); lvl=raw===null?"na":aggBlockLevel(b.id,sc); }
    var bs=raw===null?"N/A":pctRound(raw)+"%";
    var cls="bb-card"+(lvl==="review"?" review":"")+(lvl==="resurvey"?" resurvey":"");
    var fillW=raw===null?0:(lvl==="good"?100:pctRound(raw));
    var fillCol=lvl==="good"?"rgba(16,185,214,.38)":(lvl==="resurvey"?"rgba(201,64,64,.5)":(lvl==="na"?"rgba(255,255,255,.06)":"rgba(232,228,218,.18)"));
    return '<div class="'+cls+'" id="gp-'+b.id+'">'+
      '<div class="bb-header"><div class="bb-h-left">'+
        '<div class="bb-num">BB · 0'+(idx+1)+'</div>'+
        '<div class="bb-name">'+blockName(b)+'</div>'+
        '<div class="bb-weight">weight '+b.weight.toFixed(2)+'</div>'+
      '</div><div class="bb-h-right">'+
        '<div class="bb-score-sm">'+bs+'</div><div class="bb-status-dot"></div>'+
      '</div></div>'+
      '<div class="bb-inner-always">'+
        '<div class="bb-bar"><div class="bb-bar-fill" style="width:'+fillW+'%;background:'+fillCol+'"></div></div>'+
        '<div class="bb-toggle-row" onclick="dsGcp.toggleBBIndicators(\''+b.id+'\')"><span class="bb-check"></span><span class="bb-toggle-text">Show indicators</span></div>'+
        '<div class="bb-status-full">'+statusText(lvl)+'</div>'+
        '<div class="bb-details" onclick="event.stopPropagation();dsGcp.openBBDetails(\''+b.id+'\')">Details ›</div>'+
      '</div></div>';
  }).join("");
  markActiveBB();
}

/* ---- pills (one per indicator, aggregate severity across points) ---- */
function renderIndicators(){
  var layer=document.getElementById("gp-indicatorLayer");if(!layer)return;
  var sc=currentScenario,p=curPoint(),html=[];
  if(p){
    BLOCKS.forEach(function(b){
      if(!selected[b.id])return;
      var inds=blockIndicators(b.id),pts=posFor(inds.length);
      inds.forEach(function(ind,i){
        var pos=pts[i]||[50,75];
        var s=p.scores[ind.id],lvl=getBandForScore(ind,s).level;
        var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
        html.push('<div class="indicator-pill'+sev+'" style="left:'+pos[0]+'%;top:'+pos[1]+'%" title="'+ind.name+' · '+s+'"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+s+'</b></div>');
      });
    });
    layer.innerHTML=html.join("");
    layer.className="indicator-layer"+(html.length?" show":"");
    return;
  }
  if(hasPoints(sc)){
    BLOCKS.forEach(function(b){
      if(!selected[b.id])return;
      var inds=blockIndicators(b.id),pts=posFor(inds.length);
      inds.forEach(function(ind,i){
        var p=pts[i]||[50,75];
        var lvl=aggIndicatorLevel(ind,sc);
        var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
        var ws=aggIndicatorWorstScore(ind,sc);
        var wp=(lvl!=="good")?worstPointFor(ind,sc):null;
        var ptTag=wp?'<i class="ip-pt">'+wp.id+'</i>':'';
        var tip=ind.name+' · worst '+(ws===null?"—":ws)+(wp?' at '+wp.id:'');
        html.push('<div class="indicator-pill'+sev+'" style="left:'+p[0]+'%;top:'+p[1]+'%" title="'+tip+'"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+(ws===null?"—":ws)+ptTag+'</b></div>');
      });
    });
  }
  layer.innerHTML=html.join("");
  layer.className="indicator-layer"+(html.length?" show":"");
}
function toggleBBIndicators(id){selected[id]=!selected[id];markActiveBB();renderIndicators();}
function markActiveBB(){BLOCKS.forEach(function(b){var el=document.getElementById("gp-"+b.id);if(el)el.classList.toggle("active",!!selected[b.id]);});}

/* ---- per-block Details drawer (aggregate decomposition, worst-point detail) ---- */
function openBBDetails(blockId){
  selected[blockId]=true;markActiveBB();renderIndicators();
  var b=BLOCKS.filter(function(x){return x.id===blockId;})[0];
  var sc=currentScenario;
  var p=curPoint();
  if(p){
    var pbs=pctRound(computePointBlockScore(blockId,p.scores)),plvl=pointBlockLevel(blockId,p);
    var prows=blockIndicators(blockId).map(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s);
      var sevCls=band.level==="good"?"":(band.level==="resurvey"?"resurvey":(band.level==="minor"?"":"review"));
      var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
      var h='<div class="d-ind"><div class="d-ind-top">'+
        '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
        '<div class="d-ind-sc '+sevCls+'">'+s+'</div></div>'+
        '<div class="d-ind-band">'+(band.level==="good"?substitutePointId(ind.verified_statement,p.id):substitutePointId(band.label,p.id))+'</div>';
      if(band.impact)h+='<div class="d-ind-impact">'+substitutePointId(band.impact,p.id)+'</div>';
      if(band.actions)h+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,p.id)+'</li>';}).join("")+'</ul>';
      h+='<div class="d-deriv">in-block weight '+ind.weight.toFixed(2)+' · contributes '+Math.round(ind.weight*s)+'/'+Math.round(ind.weight*100)+'</div>';
      if(ind.derivation)h+='<div class="d-deriv">'+ind.derivation+'</div>';
      return h+'</div>';
    }).join("");
    var plim=null,plimv=1e9;
    blockIndicators(blockId).forEach(function(ind){var v=ind.weight*p.scores[ind.id];if(v<plimv){plimv=v;plim=ind;}});
    var plimHtml=plim&&plvl!=="good"?'<div class="d-gate" style="color:rgba(232,228,218,.6);border-color:var(--line);background:rgba(255,255,255,.014)">Limiting factor: '+plim.name+'</div>':'';
    document.getElementById("gp-drawerBody").innerHTML=
      '<h2>'+blockName(b)+'</h2>'+
      '<div class="d-score">'+pbs+'<span>%</span></div>'+
      '<div class="d-verdict '+plvl+'">'+statusText(plvl)+'  ·  '+p.id+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
      '<div class="d-narr">'+b.description+'</div>'+plimHtml+
      '<div class="d-sec">Indicators</div>'+prows;
    openDrawer();return;
  }
  var raw=aggBlockScore(blockId,sc);
  var lvl=raw===null?"na":aggBlockLevel(blockId,sc);
  var inds=blockIndicators(blockId);
  var rows=inds.map(function(ind){
    var lev=aggIndicatorLevel(ind,sc);
    var ws=aggIndicatorWorstScore(ind,sc);
    var wp=worstPointFor(ind,sc);
    var pid=wp?wp.id:"";
    var band=wp?getBandForScore(ind,ws):null;
    var sevCls=lev==="good"?"":(lev==="resurvey"?"resurvey":(lev==="minor"?"":"review"));
    var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
    var html='<div class="d-ind"><div class="d-ind-top">'+
      '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
      '<div class="d-ind-sc '+sevCls+'">'+(ws===null?"—":ws)+'</div></div>'+
      '<div class="d-ind-band">'+(lev==="good"?ind.verified_statement.replace(/at Control Point \{point_id\}/g,"at every control point").replace(/Control Point \{point_id\}/g,"Every control point").replace(/\{point_id\}/g,"every point"):substitutePointId(band.label,pid))+'</div>';
    if(band&&band.impact)html+='<div class="d-ind-impact">'+substitutePointId(band.impact,pid)+'</div>';
    if(band&&band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,pid)+'</li>';}).join("")+'</ul>';
    var contrib=ws===null?0:(ind.weight*ws);
    html+='<div class="d-deriv">in-block weight '+ind.weight.toFixed(2)+' · contributes '+(ws===null?"—":Math.round(contrib)+'/'+Math.round(ind.weight*100))+(lev!=="good"&&pid?' · worst at '+pid:"")+'</div>';
    if(ind.derivation)html+='<div class="d-deriv">'+ind.derivation+'</div>';
    return html+'</div>';
  }).join("");
  var lim=null,limv=1e9;
  inds.forEach(function(ind){var ws=aggIndicatorWorstScore(ind,sc);if(ws===null)return;var v=ind.weight*ws;if(v<limv){limv=v;lim=ind;}});
  var limHtml=lim&&lvl!=="good"&&lvl!=="na"?'<div class="d-gate" style="color:rgba(232,228,218,.6);border-color:var(--line);background:rgba(255,255,255,.014)">Limiting factor: '+lim.name+'</div>':'';
  var formHtml="";
  if(raw!==null){
    var ps2=blockPerPoint(blockId,sc),mean2=Math.round(ps2.reduce(function(a,c){return a+c;},0)/ps2.length),min2=Math.min.apply(null,ps2),pen2=Math.round(0.25*(100-min2)),wpId="",lo=1e9;
    sc.points.forEach(function(pp){var v=computePointBlockScore(blockId,pp.scores);if(v<lo){lo=v;wpId=pp.id;}});
    formHtml='<div class="d-deriv" style="margin:2px 0 12px">Aggregate = mean '+mean2+' across '+ps2.length+' points − '+pen2+' spread penalty (worst point '+wpId+' at '+Math.round(min2)+'). Select that point above for fix-level detail.</div>';
  }
  document.getElementById("gp-drawerBody").innerHTML=
    '<h2>'+blockName(b)+'</h2>'+
    '<div class="d-score">'+(raw===null?"N/A":pctRound(raw)+'<span>%</span>')+'</div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
    '<div class="d-narr">'+b.description+'</div>'+formHtml+limHtml+
    '<div class="d-sec">Indicators · worst across points</div>'+rows;
  openDrawer();
}

/* ---- per-POINT Details drawer (drill into a single Control Point) ---- */
function openPointDetails(pointId){
  var sc=currentScenario;
  var p=(sc.points||[]).filter(function(x){return x.id===pointId;})[0];if(!p)return;
  var ps=pctRound(computePointScore(p)),lvl=pointLevel(p);
  var blocksHtml=BLOCKS.map(function(b){
    var bs=pctRound(computePointBlockScore(b.id,p.scores));
    var rows=blockIndicators(b.id).map(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s);
      var sevCls=band.level==="good"?"":(band.level==="resurvey"?"resurvey":(band.level==="minor"?"":"review"));
      var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
      var html='<div class="d-ind"><div class="d-ind-top">'+
        '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
        '<div class="d-ind-sc '+sevCls+'">'+s+'</div></div>'+
        '<div class="d-ind-band">'+(band.level==="good"?substitutePointId(ind.verified_statement,p.id):substitutePointId(band.label,p.id))+'</div>';
      if(band.impact)html+='<div class="d-ind-impact">'+substitutePointId(band.impact,p.id)+'</div>';
      if(band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,p.id)+'</li>';}).join("")+'</ul>';
      html+=fmtLivePointInputs(ind,p);
      return html+'</div>';
    }).join("");
    return '<div class="d-sec">'+blockName(b)+'  ·  '+bs+'%</div>'+rows;
  }).join("");
  document.getElementById("gp-drawerBody").innerHTML=
    '<h2>'+p.id+'</h2>'+
    '<div class="d-score">'+ps+'<span>%</span></div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  '+(p.device_type||"")+' device</div>'+
    '<div class="d-narr">Per-point breakdown for '+p.id+' across all '+BLOCKS.length+' blocks. This point contributes to the averaged chain score.</div>'+
    blocksHtml;
  openDrawer();
}

/* ---- Why drawer (per-point findings) ---- */
function accFinding(f,kind,open){
  var sevCls=kind==="resurvey"?"resurvey":(kind==="review"?"review":"");
  var tag={resurvey:"Resurvey",review:"Review",noted:"Noted"}[kind];
  var pid=f.point?f.point.id:"";
  var body='<div class="acc-state">'+substitutePointId(f.band.label,pid)+'</div>'+
    (f.band.impact?'<div class="d-ind-impact">'+substitutePointId(f.band.impact,pid)+'</div>':'')+
    (f.band.actions?'<ul class="d-acts">'+f.band.actions.map(function(a){return'<li>'+substitutePointId(a,pid)+'</li>';}).join("")+'</ul>':'');
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+f.indicator.name+
      (pid?' <span style="color:rgba(232,228,218,.5)">· '+pid+'</span>':"")+'</span>'+
    '<span class="acc-right"><span class="acc-sc '+sevCls+'">'+f.score+'</span></span>'+
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function accVerified(ind,scenario,open){
  var n=scenario.points.length;
  var body='<div class="acc-state">'+ind.verified_statement.replace(/at Control Point \{point_id\}/g,"at every control point").replace(/Control Point \{point_id\}/g,"Every control point").replace(/\{point_id\}/g,"every point")+'</div>'+
    '<div class="acc-evi">Evidence · good across all '+n+' point'+(n===1?"":"s")+'</div>';
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+ind.name+'</span>'+
    
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function setSection(sel,open){var rows=document.querySelectorAll(sel+" .acc");for(var i=0;i<rows.length;i++)rows[i].classList.toggle("open",open);}

function accPointVerified(ind,band,p,open){
  var body='<div class="acc-state">'+substitutePointId(ind.verified_statement,p.id)+'</div>'+
    '<div class="acc-evi">Evidence · '+band.label+'</div>'+fmtLivePointInputs(ind,p);
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+ind.name+'</span>'+
    '<span class="acc-right"><span class="acc-sc">'+p.scores[ind.id]+'</span></span>'+
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function verifiedBlock(count,listHtml){
  var head='<div class="d-sec-row"><div class="d-sec verified">Verified<span class="d-sec-count">'+count+'</span></div></div>';
  if(count<=0) return head+'<div id="gp-verSec">'+listHtml+'</div>';
  var summary=(typeof INDICATORS!=='undefined'&&count===INDICATORS.length)?('All '+count+' indicators passed verification.'):(count+' indicators verified and in good standing.');
  return '<div class="d-sec-row"><div style="display:flex;align-items:baseline;gap:10px;flex:1;min-width:0"><span class="d-sec verified" style="margin:0;padding:0;border:0;flex-shrink:0">Verified</span><span class="d-empty" style="padding:0">'+summary+'</span></div><button class="d-ctrl" id="gp-verToggle" onclick="dsGcp.toggleVerified()" style="flex-shrink:0">+ More Details</button></div>'+'<div id="gp-verSec" style="display:none">'+listHtml+'</div>';
}
function toggleVerified(){
  var sec=document.getElementById('gp-verSec'),tog=document.getElementById('gp-verToggle');
  if(!sec||!tog)return;
  var open=(sec.style.display==='none');
  sec.style.display=open?'block':'none';
  tog.innerHTML=open?'\u2212 Show less':'+ More Details';
}
function openRecommendation(){
  var sc=currentScenario,p=curPoint();
  if(p){
    var pr=pointRec(p),act=[],ver=[],noted=[];
    INDICATORS.forEach(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s),sev=severityForScore(ind,s);
      if(sev==="critical")act.push({indicator:ind,score:s,band:band,point:p,kind:"resurvey",rank:0});
      else if(sev==="material")act.push({indicator:ind,score:s,band:band,point:p,kind:"review",rank:1});
      else if(sev==="minor")noted.push({indicator:ind,score:s,band:band,point:p});
      else ver.push({indicator:ind,band:band});
    });
    act.sort(function(a,b){return a.rank-b.rank||importance(b.indicator)-importance(a.indicator);});
    ver.sort(function(a,b){return importance(b.indicator)-importance(a.indicator);});
    var vOpen=act.length===0;
    var actH=act.length?act.map(function(f){return accFinding(f,f.kind,true);}).join(""):'<div class="d-empty">Nothing to action — this point has no Review or Resurvey findings.</div>';
    var notedH=noted.map(function(f){return accFinding(f,"noted",false);}).join("");
    var verH=ver.length?ver.map(function(f){return accPointVerified(f.indicator,f.band,p,false);}).join(""):'<div class="d-empty">No indicator passed cleanly at this point.</div>';
    document.getElementById("gp-drawerBody").innerHTML=
      '<h2>Why '+(REC_LABEL[pr.rec]==="GOOD TO GO"?"Good to go":REC_LABEL[pr.rec])+'?</h2>'+
      
      
      '<div class="d-narr">'+POINT_REASON[pr.rec]+'</div>'+
      '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+act.length+'</span></div>'+
        '<div class="d-ctrls"><button class="d-ctrl" onclick="dsGcp.setSection(\'#gp-actSec\',true)">Expand all</button>'+
        '<button class="d-ctrl" onclick="dsGcp.setSection(\'#gp-actSec\',false)">Collapse all</button></div></div>'+
      '<div id="gp-actSec">'+actH+notedH+'</div>'+
      verifiedBlock(ver.length, verH);
    openDrawer();return;
  }
  var rec=overallRecommendation(sc),ov=rec.overall;
  if(ov.status==="NOT_APPLICABLE"){
    document.getElementById("gp-drawerBody").innerHTML=
      '<h2>Why Not applicable?</h2>'+
      
      '<div class="d-narr">'+REC_REASON.na+'</div>'+
      '<div class="d-empty">No Control Points were designated for this survey, so there are no point occupations to score. Control comes from the PPK / PPP workflow instead.</div>';
    openDrawer();return;
  }
  var findings=rankFindings(sc);                 // review + resurvey, per point, pre-sorted
  var noted=minorFindings(sc);                   // minor/hygiene, per point
  var verified=rankVerified(sc);                 // indicators passing all points
  var gateHtml=ov.hardGate?'<div class="d-gate">HARD GATE — '+GLOBAL_GATE_CONDITION+'</div>':'';
  var verifiedOpen=findings.length===0;
  var actHtml=findings.length
    ? findings.map(function(f){return accFinding(f,f.band.level==="resurvey"?"resurvey":"review",true);}).join("")
    : '<div class="d-empty">Nothing to action — no point has a Review or Resurvey finding.</div>';
  var notedHtml=noted.map(function(f){return accFinding(f,"noted",false);}).join("");
  var verHtml=verified.length
    ? verified.map(function(c){return accVerified(c.indicator,sc,false);}).join("")
    : '<div class="d-empty">No indicator passed cleanly across all points.</div>';
  document.getElementById("gp-drawerBody").innerHTML=
    '<h2>Why '+(REC_LABEL[rec.rec]==="GOOD TO GO"?"Good to go":REC_LABEL[rec.rec])+'?</h2>'+
    
    
    '<div class="d-narr">'+REC_REASON[rec.rec]+'</div>'+gateHtml+
    '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+findings.length+'</span></div>'+
      '<div class="d-ctrls"><button class="d-ctrl" onclick="dsGcp.setSection(\'#gp-actSec\',true)">Expand all</button>'+
      '<button class="d-ctrl" onclick="dsGcp.setSection(\'#gp-actSec\',false)">Collapse all</button></div></div>'+
    '<div id="gp-actSec">'+actHtml+notedHtml+'</div>'+
    verifiedBlock(verified.length, verHtml);
  openDrawer();
}

function openDrawer(){document.getElementById("gp-drawer").classList.add("open");}
function closeDrawer(){document.getElementById("gp-drawer").classList.remove("open");}
var GCP_API_READY=false;
function renderGcpNoApi(msg){
  var score=document.getElementById("gp-scoreNum"); if(score) score.innerHTML='<span style="font-size:28px;opacity:.45;letter-spacing:.1em">NO API DATA</span>';
  var delta=document.getElementById("gp-scoreDelta"); if(delta) delta.textContent=msg||"Start the API and refresh the database.";
  var reason=document.getElementById("gp-mdReason"); if(reason) reason.textContent=msg||"No GCP API data loaded.";
  var pick=document.getElementById("gp-scnPick"); if(pick) pick.innerHTML="";
  var points=document.getElementById("gp-pointSelect"); if(points) points.innerHTML='<option>No API data</option>';
  var cards=document.getElementById("gp-bbStripHead"); if(cards) cards.innerHTML='<div class="d-empty">No GCP records returned by the API.</div>';
  var layer=document.getElementById("gp-indicatorLayer"); if(layer){layer.innerHTML="";layer.className="indicator-layer";}
}
function renderAll(){
  if(!GCP_API_READY){renderGcpNoApi();return;}
  renderScenarioPicker();renderPointSelect();renderHeadline();renderBBCards();renderIndicators();
}

var REAL_OVERALL=(function(){var v=aggOverallCanon(currentScenario);return v==null?0:v;})();
window.dsGcp={openTrend:openTrend,closeTrend:closeTrend,toggleFleet:toggleFleet,
  toggleBBSection:toggleBBSection,selectScenario:selectScenario,selectPoint:selectPoint,
  toggleBBIndicators:toggleBBIndicators,openBBDetails:openBBDetails,openPointDetails:openPointDetails,
  openRecommendation:openRecommendation,closeDrawer:closeDrawer,
  setSection:setSection,toggleVerified:toggleVerified,render:renderAll,
  refreshApi:function(){ if(!GCP_API_READY) loadLiveGcpScores(); },
  realScore:REAL_OVERALL};

/* ============================================================
   LIVE DATA — fetch from /api/indicators and hydrate Control Point UI
   Endpoint returns an array of per-point traces:
   [{ point_id, device_role, device_type, indicator_traces: {
        "L3I_GCP_NNN_<name>": { score, input_values, band_matched, ... }, ... } }, ...]
   ============================================================ */

var GCP_API_URL = loopApiUrl("/api/gcp/indicators");
var GCP_API_RETRY_COUNT=0;
var GCP_API_RETRY_MAX=240;
var GCP_API_RETRY_MS=3000;
var GCP_API_LOADING=false;

/* ---- live input_values formatter (per point, per indicator) ---- */
function fmtLivePointInputs(ind,p){
  if(!p || !p._liveInputs || !p._liveInputs[ind.id]) return "";
  var inputs = p._liveInputs[ind.id];
  var pairs = Object.entries
    ? Object.entries(inputs)
    : Object.keys(inputs).map(function(k){ return [k, inputs[k]]; });
  if(!pairs.length) return "";
  var items = pairs.map(function(kv){
    var k=kv[0].replace(/_/g," "), v=kv[1];
    if(v===null||v===undefined) v="—";
    else if(typeof v==="boolean") v=v?"yes":"no";
    else if(typeof v==="object") v=JSON.stringify(v);
    return '<span class="live-kv"><span class="live-k">'+k+'</span><span class="live-v">'+v+'</span></span>';
  }).join("");
  return '<div class="live-inputs">'+items+'</div>';
}

function gcpShowLoadingState(){
  var el=document.getElementById("gp-scoreNum");
  if(el) el.innerHTML='<span style="font-size:28px;opacity:.4;letter-spacing:.1em">LOADING</span>';
}

function gcpShowErrorBadge(msg){
  var badge=document.createElement("div");
  badge.style.cssText=[
    "position:fixed;bottom:18px;left:50%;transform:translateX(-50%)",
    "background:rgba(201,64,64,.18);border:1px solid rgba(201,64,64,.4)",
    "color:rgba(232,228,218,.7);font-family:var(--fm);font-size:10px",
    "letter-spacing:.12em;padding:6px 14px;border-radius:2px;z-index:9999",
    "pointer-events:none"
  ].join(";");
  badge.textContent="GCP API UNAVAILABLE - no live data loaded  ·  "+msg;
  document.body.appendChild(badge);
  setTimeout(function(){ badge.remove(); },6000);
}

/* Map one API point object -> {id, device_type, scores:{L3I_GCP_NNN:n}, _liveInputs:{...}} */
function mapApiPoint(apiPoint){
  var scores={}, liveInputs={};
  var traces=apiPoint.indicator_traces||{};
  Object.keys(traces).forEach(function(key){
    var t=traces[key];
    var id=t.indicator_id || key.split("_").slice(0,3).join("_"); // fallback: "L3I_GCP_001"
    scores[id]=t.score;
    liveInputs[id]=t.input_values||{};
  });
  return {
    id: apiPoint.point_id || ("CP-"+Math.random().toString(36).slice(2,7)),
    device_type: apiPoint.device_type || "",
    scores: scores,
    _liveInputs: liveInputs
  };
}

function injectLiveGcpScenario(apiPoints){
  var points=apiPoints.map(mapApiPoint);
  var liveScenario={
    id:"live", name:"Live", desc:"Live data from /api/indicators",
    points: points,
    _live:true
  };
  SCENARIOS.splice(0, SCENARIOS.length, liveScenario);
  currentScenario=liveScenario;
  GCP_API_READY=true;
  currentPoint=null;
  selected={};
}

function loadLiveGcpScores(){
  if(GCP_API_LOADING) return;
  GCP_API_LOADING=true;
  gcpShowLoadingState();
  fetch(withCacheBust(GCP_API_URL),{cache:'no-store'})
    .then(function(res){
      if(!res.ok) throw new Error("HTTP "+res.status);
      return res.json();
    })
    .then(function(data){
      GCP_API_LOADING=false;
      var points=Array.isArray(data) ? data : (data.points||data.indicators||data);
      if(!Array.isArray(points)||points.length===0) throw new Error("empty points array");
      GCP_API_RETRY_COUNT=0;
      injectLiveGcpScenario(points);
      renderAll();
    })
    .catch(function(err){
      GCP_API_LOADING=false;
      if(GCP_API_RETRY_COUNT===0 || GCP_API_RETRY_COUNT%20===0) gcpShowErrorBadge(err.message||String(err));
      GCP_API_READY=false;
      renderGcpNoApi(err.message||String(err));
      if(GCP_API_RETRY_COUNT<GCP_API_RETRY_MAX){
        GCP_API_RETRY_COUNT++;
        setTimeout(loadLiveGcpScores,GCP_API_RETRY_MS);
      }
    });
}

loadLiveGcpScores();

})();

/* ── GCP → GLOBAL CONFIDENCE wiring (single real state = review/Mixed Quality) ── */
(function(){
  var real = (window.dsGcp && typeof window.dsGcp.realScore==='number')
             ? Math.round(window.dsGcp.realScore) : 96;
  if(typeof SUB_CAPTURE_GCP!=='undefined') SUB_CAPTURE_GCP.score = real;
  var W={drone:0.35, base:0.30, gcp:0.20, preproc:0.15};
  var sc={
    drone:(typeof SUB_CAPTURE_DRONE!=='undefined')?SUB_CAPTURE_DRONE.score:95,
    base:(typeof SUB_CAPTURE_BASE!=='undefined')?SUB_CAPTURE_BASE.score:87,
    gcp:real,
    preproc:(typeof SUB_CAPTURE_PREPROC!=='undefined')?SUB_CAPTURE_PREPROC.score:90
  };
  var capScore=Math.round(W.drone*sc.drone+W.base*sc.base+W.gcp*sc.gcp+W.preproc*sc.preproc);
  if(typeof ONTOLOGY!=='undefined' && ONTOLOGY.universes && ONTOLOGY.universes[0]){
    ONTOLOGY.universes[0].score=capScore;
    if(typeof GATES!=='undefined' && GATES[0]){GATES[0].score=capScore;if(GATES[0].universe)GATES[0].universe.score=capScore;}
    var nOJS=Math.round(ONTOLOGY.universes[0].score*ONTOLOGY.universes[0].weight +
      ONTOLOGY.universes[1].score*ONTOLOGY.universes[1].weight +
      ONTOLOGY.universes[2].score*ONTOLOGY.universes[2].weight);
    var ms=document.getElementById('ms-num');
    if(ms) ms.innerHTML=nOJS+'<span style="font-size:.28em;font-weight:700;color:rgba(235,242,248,.38);vertical-align:super;line-height:0;">%</span>';
    var st=document.getElementById('sentence-text');
    if(st) st.innerHTML='Pitpack 4 scored <strong>'+nOJS+'%</strong> on the Infinity Loop &mdash; up 2.3% from last survey, trending toward Professional Grade across 11 missions.';
    if(typeof buildScoreLabels==='function'){try{buildScoreLabels();}catch(e){}}
  }
})();
buildGcpPage = function(){
  if(window.dsGcp){
    if(window.dsGcp.refreshApi) window.dsGcp.refreshApi();
    window.dsGcp.render();
  }
};


/* ═══════════════════════════════════════════════
   CHECK POINT (DATUM hero) — locked per-point RTK chain + UI, namespaced via window.dsCp
   Data verbatim from check_point_multi_view_v1_LOCKED; per-point engine + gates adapted
   ═══════════════════════════════════════════════ */
(function(){
const BLOCKS = [
  {
    "id": "BB_CP_COMPLETE",
    "name": "Capture Completeness & Integrity",
    "weight": 0.45,
    "description": "Position sigma, fix type, correction age, and log integrity at the check-point."
  },
  {
    "id": "BB_CP_SETUP",
    "name": "Setup & Documentation Confidence",
    "weight": 0.35,
    "description": "Antenna height, pole stability, baseline length, NTRIP, antenna type match, device ID traceability."
  },
  {
    "id": "BB_CP_ENV",
    "name": "Observation Environment",
    "weight": 0.2,
    "description": "PDOP, fix hold, sky obstruction, ionospheric risk at measurement epoch."
  }
];

const GLOBAL_GATE_CONDITION = "(every CHECK_POINT-role point has cp_fix_type_score == 0) OR (every CHECK_POINT-role point has cp_position_sigma_score == 0)";

// ============================================================
// INDICATOR LIBRARY — single source of truth (Q1, Q2, Q4 locks applied)
// ============================================================
const INDICATOR_LIBRARY = {
  "L3I_CP_001": {
    "id": "L3I_CP_001",
    "num": "#01",
    "block": "BB_CP_COMPLETE",
    "weight": 0.45,
    "name": "Position sigma",
    "fullName": "cp_position_sigma_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "RTK receiver reported position uncertainty (sigma) is within acceptable range.",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Position sigma \u22645cm (excellent RTK convergence)",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          50,
          84
        ],
        "level": "review",
        "label": "Position sigma 5-15cm (moderate RTK convergence)",
        "impact": "RTK locked but with visible uncertainty. Output position has larger error envelope. Acceptable for most survey classes but check whether specification is met.",
        "actions": [
          "Verify final deliverable accuracy against project specification",
          "Check whether baseline length or multipath affected convergence",
          "Document sigma value in metadata for downstream reference"
        ]
      },
      {
        "score_range": [
          0,
          49
        ],
        "level": "review",
        "label": "Position sigma >15cm or no fix (degraded RTK)",
        "impact": "RTK either did not converge fully or reported large uncertainty. Position accuracy is compromised; deliverable may not meet specification.",
        "actions": [
          "Inspect RTK log for fix-type history and base distance",
          "Check whether multipath or obstructions degraded signal",
          "Consider whether re-occupying under clearer conditions is practical"
        ]
      }
    ],
    "derivation": "Sigma is the anchor indicator for RTK quality. \u22645cm is industry 'good RTK', 5-15cm is 'acceptable with notes', >15cm is 'degraded'.",
    "flag": "CP_SIGMA_REJECT"
  },
  "L3I_CP_002": {
    "id": "L3I_CP_002",
    "num": "#02",
    "block": "BB_CP_COMPLETE",
    "weight": 0.3,
    "name": "Fix type at capture",
    "fullName": "cp_fix_type_score",
    "is_critical_path": false,
    "gate_scope": "per_point_only",
    "verified_statement": "RTK receiver had a FIXED integer ambiguity at the measurement epoch (not FLOAT or AUTONOMOUS).",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "FIXED RTK \u2014 integer ambiguity resolved",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          84
        ],
        "level": "critical",
        "label": "FLOAT or AUTONOMOUS (per-point gate fires \u2014 Q-CP-2)",
        "impact": "RTK did not achieve fixed integer ambiguity. Position is FLOAT (meter-level uncertainty) or AUTONOMOUS (GPS-only, 3-5m uncertainty). This point's score is zeroed per-point aggregation. Other points continue to score.",
        "actions": [
          "Inspect RTK log for why fix was not achieved",
          "Check base station corrections availability at the time",
          "If repeated, consider occupying this location longer or from different setup"
        ]
      }
    ],
    "derivation": "Q-CP-2: FLOAT/AUTONOMOUS promoted to per-point gate. Affects individual point; aggregation dilutes across fleet. Not a chain-level hard gate.",
    "flag": "CP_FLOAT_ACCEPTED_AS_FIXED"
  },
  "L3I_CP_003": {
    "id": "L3I_CP_003",
    "num": "#03",
    "block": "BB_CP_COMPLETE",
    "weight": 0.15,
    "name": "Correction age",
    "fullName": "cp_correction_age_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Base station corrections at measurement epoch were recent (not stale).",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Corrections <30s old at measurement",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "minor",
        "label": "Corrections >30s old (hygiene signal)",
        "impact": "RTK used aged corrections. Slight degradation in convergence quality; usually still acceptable but less optimal.",
        "actions": [
          "Check whether base station or NTRIP link was temporarily unstable",
          "Inspect whether using a farther base would have better correction freshness"
        ]
      }
    ],
    "derivation": "Correction age >30s means increased atmospheric modeling error in base computation.",
    "flag": "CP_CORRECTION_AGE_STALE"
  },
  "L3I_CP_004": {
    "id": "L3I_CP_004",
    "num": "#04",
    "block": "BB_CP_COMPLETE",
    "weight": 0.1,
    "name": "Log integrity",
    "fullName": "cp_log_integrity_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "RTK log file was downloaded cleanly and has valid checksum/signature.",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Log downloaded, checksum valid",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          84
        ],
        "level": "minor",
        "label": "Log download incomplete or checksum failed (hygiene signal)",
        "impact": "Log file may be corrupted or truncated. Data is still usable but requires inspection for gap or corruption.",
        "actions": [
          "Attempt to download log again from device",
          "Inspect log file directly for obvious gaps or truncation",
          "Verify device storage and connectivity for next occupation"
        ]
      }
    ],
    "derivation": "Hygiene signal. Log integrity check catches storage failures early.",
    "flag": null
  },
  "L3I_CP_005": {
    "id": "L3I_CP_005",
    "num": "#05",
    "block": "BB_CP_SETUP",
    "weight": 0.4,
    "name": "Antenna height documented",
    "fullName": "cp_antenna_height_documented_score",
    "is_critical_path": false,
    "gate_scope": "per_point_only",
    "verified_statement": "Antenna height measurement was recorded and is traceable.",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Antenna height measured and documented",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "critical",
        "label": "Antenna height not documented (per-point gate)",
        "impact": "Height is unknown. Phase center offset cannot be corrected; vertical accuracy is compromised. This point's score is zeroed per aggregation.",
        "actions": [
          "Inspect field photo or notes for height evidence",
          "If unavailable, mark this point as un-usable for final coordinates",
          "For next occupation, enforce height measurement protocol"
        ]
      }
    ],
    "derivation": "Q-CP-4: no device-type special handling. All devices must have documented height (unlike Control Point where factory-known devices auto-pass).",
    "flag": "CP_ANTENNA_HEIGHT_MISSING"
  },
  "L3I_CP_006": {
    "id": "L3I_CP_006",
    "num": "#06",
    "block": "BB_CP_SETUP",
    "weight": 0.2,
    "name": "Pole stability",
    "fullName": "cp_pole_stability_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Antenna pole/rod was stable throughout measurement (no movement, no tilt).",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Pole stable, antenna plumb, no observed movement",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          40,
          84
        ],
        "level": "review",
        "label": "Pole had some movement or slight tilt",
        "impact": "Antenna may have shifted during measurement. Position reported is averaged over an unstable baseline; accuracy may be degraded.",
        "actions": [
          "Inspect field notes/photos for movement evidence",
          "Check whether RTK fix remained constant despite movement",
          "Consider whether re-occupying with more stable setup is practical"
        ]
      },
      {
        "score_range": [
          0,
          39
        ],
        "level": "review",
        "label": "Pole unstable or tilted significantly",
        "impact": "Antenna position changed during measurement. Reported position is unreliable; deliverable may not meet specification.",
        "actions": [
          "Mark point as requiring re-occupation with stable setup",
          "Review field protocol for pole stability checks"
        ]
      }
    ],
    "derivation": "Movement during occupation violates RTK assumption (static receiver).",
    "flag": "CP_POLE_INSTABILITY"
  },
  "L3I_CP_007": {
    "id": "L3I_CP_007",
    "num": "#07",
    "block": "BB_CP_SETUP",
    "weight": 0.15,
    "name": "Baseline length",
    "fullName": "cp_baseline_length_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Distance to base station or CORS reference is reasonable for RTK convergence.",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Baseline \u226450 km to base/CORS (standard RTK range)",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "review",
        "label": "Baseline >50 km (extended range, slower convergence)",
        "impact": "RTK required longer convergence time. Fix quality may be weaker; uncertainty may be larger than nominal.",
        "actions": [
          "Check whether a closer base/CORS was available",
          "Verify convergence time matches distance expectation",
          "Inspect final sigma for degradation"
        ]
      }
    ],
    "derivation": "RTK atmospheric modeling errors grow with baseline. >50 km requires extended convergence.",
    "flag": "CP_BASELINE_LONG"
  },
  "L3I_CP_008": {
    "id": "L3I_CP_008",
    "num": "#08",
    "block": "BB_CP_SETUP",
    "weight": 0.1,
    "name": "NTRIP mountpoint",
    "fullName": "cp_ntrip_mountpoint_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "NTRIP correction stream was correctly matched to location and receiver type.",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Correct NTRIP mountpoint for location and receiver",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          84
        ],
        "level": "review",
        "label": "NTRIP mountpoint mismatch (wrong region, wrong format)",
        "impact": "Corrections may not apply well to location. RTK convergence is suboptimal; final accuracy may not meet specification.",
        "actions": [
          "Verify NTRIP stream coverage for the location",
          "Check whether receiver is compatible with correction format",
          "Use correct mountpoint for next occupation"
        ]
      }
    ],
    "derivation": "NTRIP mismatch degrades RTK performance.",
    "flag": "CP_NTRIP_MISMATCH"
  },
  "L3I_CP_009": {
    "id": "L3I_CP_009",
    "num": "#09",
    "block": "BB_CP_SETUP",
    "weight": 0.1,
    "name": "Antenna type consistency",
    "fullName": "cp_antenna_type_match_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Antenna type matches documented type in receiver configuration.",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Antenna type matches receiver config",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "review",
        "label": "Antenna type mismatch",
        "impact": "Phase center offset model may be incorrect. Vertical accuracy especially is compromised.",
        "actions": [
          "Verify actual antenna installed vs receiver configuration",
          "Update receiver configuration to match actual antenna",
          "If old data, apply post-correction with correct antenna model"
        ]
      }
    ],
    "derivation": "Type mismatch causes incorrect phase center modeling.",
    "flag": "CP_ANTENNA_TYPE_MISMATCH"
  },
  "L3I_CP_010": {
    "id": "L3I_CP_010",
    "num": "#10",
    "block": "BB_CP_SETUP",
    "weight": 0.05,
    "name": "Device ID provenance",
    "fullName": "cp_device_id_match_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Device serial number in log matches expected device (traceability).",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Device ID traceable and matches expected unit",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          84
        ],
        "level": "minor",
        "label": "Device ID mismatch or not traceable (hygiene)",
        "impact": "Audit trail unclear. Cannot verify which physical device was used.",
        "actions": [
          "Inspect field notes or device label to verify serial number",
          "For next occupation, confirm device serial before and after use"
        ]
      }
    ],
    "derivation": "Hygiene/audit signal.",
    "flag": null
  },
  "L3I_CP_011": {
    "id": "L3I_CP_011",
    "num": "#11",
    "block": "BB_CP_ENV",
    "weight": 0.4,
    "name": "PDOP at capture",
    "fullName": "cp_pdop_at_capture_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Satellite geometry at measurement epoch was favorable (PDOP within RTK range).",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "PDOP <3 at capture (excellent geometry)",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          40,
          74
        ],
        "level": "review",
        "label": "PDOP 3-6 (acceptable but not ideal)",
        "impact": "Satellite geometry was suboptimal. Position uncertainty elevated but usually acceptable.",
        "actions": [
          "Check PDOP forecast for future flights",
          "Inspect final sigma for degradation related to PDOP"
        ]
      },
      {
        "score_range": [
          0,
          39
        ],
        "level": "review",
        "label": "PDOP >6 (poor geometry)",
        "impact": "Satellite geometry was poor. RTK output reliability is questionable; final accuracy may not meet specification.",
        "actions": [
          "Check whether conditions were forecast; reschedule if possible",
          "Inspect final sigma against tolerance"
        ]
      }
    ],
    "derivation": "PDOP <3 is RTK baseline. >6 indicates poor sky view or unfavorable geometry.",
    "flag": "CP_PDOP_POOR"
  },
  "L3I_CP_012": {
    "id": "L3I_CP_012",
    "num": "#12",
    "block": "BB_CP_ENV",
    "weight": 0.25,
    "name": "Fix hold duration",
    "fullName": "cp_fix_hold_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "RTK maintained fixed integer solution continuously through measurement window.",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Fixed RTK throughout, no cycle slips",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "review",
        "label": "Cycle slips or brief float detected",
        "impact": "RTK lost fix momentarily. Position average may be slightly degraded; accuracy envelope larger than nominal.",
        "actions": [
          "Inspect RTK log for cause of slip (multipath, signal loss, etc.)",
          "Check environment for temporary obstruction or interference",
          "Re-occupy if accuracy-critical"
        ]
      }
    ],
    "derivation": "Cycle slips indicate RTK stability issue. Continuous fix is baseline expectation.",
    "flag": "CP_CYCLE_SLIP"
  },
  "L3I_CP_013": {
    "id": "L3I_CP_013",
    "num": "#13",
    "block": "BB_CP_ENV",
    "weight": 0.2,
    "name": "Sky obstruction",
    "fullName": "cp_obstruction_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Clear sky view above antenna \u2014 no significant obstruction (buildings, trees, hills).",
    "bands": [
      {
        "score_range": [
          85,
          100
        ],
        "level": "good",
        "label": "Clear sky, >30\u00b0 elevation minimum",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          0,
          74
        ],
        "level": "review",
        "label": "Obstructed sky view (<30\u00b0 elevation minimum)",
        "impact": "Low satellite count. RTK convergence slower; final uncertainty larger. RTK may not achieve fixed solution.",
        "actions": [
          "Relocate antenna away from obstructions if possible",
          "Allow longer convergence time at obstructed location",
          "Consider whether alternate location with clearer sky is practical"
        ]
      }
    ],
    "derivation": "RTK requires >4-5 satellites consistently. Obstruction reduces count and quality.",
    "flag": "CP_SKY_OBSTRUCTED"
  },
  "L3I_CP_014": {
    "id": "L3I_CP_014",
    "num": "#14",
    "block": "BB_CP_ENV",
    "weight": 0.15,
    "name": "Ionospheric risk",
    "fullName": "cp_ionospheric_risk_score",
    "is_critical_path": false,
    "gate_scope": "none",
    "verified_statement": "Ionospheric conditions during measurement were within historical normal range.",
    "bands": [
      {
        "score_range": [
          75,
          100
        ],
        "level": "good",
        "label": "Kp <5 (quiet ionosphere)",
        "impact": null,
        "actions": null
      },
      {
        "score_range": [
          40,
          74
        ],
        "level": "review",
        "label": "Kp 5-7 (active ionosphere, higher variance)",
        "impact": "Ionospheric modeling error elevated. RTK convergence may be slower; final uncertainty slightly larger.",
        "actions": [
          "Check NOAA SWPC Kp forecast for future surveys; avoid high Kp if possible",
          "Allow longer convergence time during active ionosphere"
        ]
      },
      {
        "score_range": [
          0,
          39
        ],
        "level": "review",
        "label": "Kp >7 (geomagnetic storm, significant ionospheric disturbance)",
        "impact": "Ionospheric disturbance was severe. RTK performance degraded; final accuracy likely compromised.",
        "actions": [
          "Reschedule critical observations to quieter periods if possible",
          "Inspect final accuracy against specification"
        ]
      }
    ],
    "derivation": "NOAA SWPC Kp index from external API. High ionospheric disturbance adds modeling error to RTK.",
    "flag": "CP_IONOSPHERIC_STORM"
  }
};

const INDICATORS = Object.values(INDICATOR_LIBRARY);

// ============================================================
// SCENARIOS — per-point fleets (illustrative, constructed from the
// locked scenario descriptions; bands/blocks/weights/gates are canonical)
// ============================================================
var _GOOD = {L3I_CP_001:98,L3I_CP_002:100,L3I_CP_003:97,L3I_CP_004:98,L3I_CP_005:96,L3I_CP_006:95,L3I_CP_007:96,L3I_CP_008:97,L3I_CP_009:96,L3I_CP_010:97,L3I_CP_011:95,L3I_CP_012:96,L3I_CP_013:95,L3I_CP_014:94};
function _pt(id, dev, ov){ var sc={}; for(var k in _GOOD) sc[k]=_GOOD[k]; if(ov){ for(var k2 in ov) sc[k2]=ov[k2]; } return {id:id, device_type:dev, scores:sc}; }

const SCENARIOS = [
  { id:"all_fixed", name:"All Points Fixed RTK", picker:"FIXED",
    desc:"Every check point achieved FIXED RTK with good sigma.",
    points:[ _pt("CP-001","CB_X"), _pt("CP-002","CB_X"), _pt("CP-003","DGPS"), _pt("CP-004","CB_X"),
             _pt("CP-005","AEROPOINT"), _pt("CP-006","CB_X"), _pt("CP-007","DGPS"), _pt("CP-008","CB_X") ] },
  { id:"mixed", name:"Mixed Quality", picker:"MIXED",
    desc:"CP-002 captured FLOAT (per-point gate fires); CP-003 has elevated position sigma. Other points clean.",
    points:[ _pt("CP-001","CB_X"),
             _pt("CP-002","CB_X", {L3I_CP_002:0}),
             _pt("CP-003","DGPS", {L3I_CP_001:45}),
             _pt("CP-004","CB_X"),
             _pt("CP-005","AEROPOINT"),
             _pt("CP-006","CB_X"),
             _pt("CP-007","DGPS"),
             _pt("CP-008","CB_X") ] },
  { id:"all_float", name:"Global Gate — All FLOAT", picker:"ALL FLOAT",
    desc:"Every check point captured FLOAT. Global gate condition met — check_point_score = 0.",
    points:[ _pt("CP-001","CB_X",{L3I_CP_002:0}), _pt("CP-002","CB_X",{L3I_CP_002:0}),
             _pt("CP-003","DGPS",{L3I_CP_002:0}), _pt("CP-004","CB_X",{L3I_CP_002:0}),
             _pt("CP-005","AEROPOINT",{L3I_CP_002:0}), _pt("CP-006","CB_X",{L3I_CP_002:0}),
             _pt("CP-007","DGPS",{L3I_CP_002:0}), _pt("CP-008","CB_X",{L3I_CP_002:0}) ] },
  { id:"no_checkpoints", name:"No Check Points", picker:"NO CP", no_points:true, points:[] }
];

// ============================================================
// LIBRARY HELPERS
// ============================================================
function getBandForScore(indicator, score) {
  for (const band of indicator.bands) { const [lo, hi] = band.score_range; if (score >= lo && score <= hi) return band; }
  return indicator.bands[indicator.bands.length - 1];
}
function severityForBand(band) {
  if (band.level === "resurvey") return "critical";
  if (band.level === "critical") return "critical";
  if (band.level === "review")   return "material";
  if (band.level === "minor")    return "minor";
  return "none";
}
function severityForScore(indicator, score) { return severityForBand(getBandForScore(indicator, score)); }
function scoreLevel(score) { if (score === 0) return "resurvey"; if (score >= 75) return "good"; if (score >= 50) return "review"; return "resurvey"; }
function substitutePointId(text, pointId) { if (!text) return text; return text.replace(/\{point_id\}/g, pointId); }

// ============================================================
// SCORING — per-point chain (check_point: 2 per-point gates + complex global gate)
// ============================================================
// A point is per-point gated when a gate_scope="per_point_only" indicator lands in its critical band
// (Q-CP-2: FLOAT/AUTONOMOUS fix type, or antenna height not documented). A gated point is unusable
// and contributes 0 to every block in the aggregation.
function pointPerPointGated(scores) {
  for (const i of INDICATORS) {
    if (i.gate_scope === "per_point_only" && severityForScore(i, scores[i.id]) === "critical") return true;
  }
  return false;
}
function computePointBlockScore(blockId, pointScores) {
  if (pointPerPointGated(pointScores)) return 0;
  const inds = INDICATORS.filter(i => i.block === blockId);
  let totalW = 0, sumW = 0;
  for (const i of inds) { const s = pointScores[i.id]; if (s === undefined) continue; totalW += i.weight; sumW += i.weight * s; }
  return totalW > 0 ? sumW / totalW : 0;
}
function computePointScore(point) {
  if (pointPerPointGated(point.scores)) return 0;
  let totalW = 0, sumW = 0;
  for (const b of BLOCKS) { const bs = computePointBlockScore(b.id, point.scores); totalW += b.weight; sumW += b.weight * bs; }
  return totalW > 0 ? sumW / totalW : 0;
}
// Complex global gate (Q-CP-1): every point FLOAT (fix_type critical) OR every point sigma == 0
function checkChainHardGate(points) {
  if (points.length === 0) return { fired: false };
  const fixInd = INDICATOR_LIBRARY["L3I_CP_002"];
  const allFloat = points.every(p => severityForScore(fixInd, p.scores["L3I_CP_002"]) === "critical");
  const allSigmaZero = points.every(p => p.scores["L3I_CP_001"] === 0);
  return { fired: allFloat || allSigmaZero };
}
function computeChainScore(scenario) {
  if (scenario.no_points || !scenario.points || scenario.points.length === 0) return { score: null, status: "NOT_APPLICABLE", hardGate: false };
  const gate = checkChainHardGate(scenario.points);
  if (gate.fired) return { score: 0, status: "HARD_GATE_FIRED", hardGate: true };
  const pointScores = scenario.points.map(computePointScore);
  return { score: pointScores.reduce((a,b)=>a+b,0)/pointScores.length, status: "NORMAL", hardGate: false };
}
// Recommendation: only the GLOBAL gate forces resurvey; a per-point gate is localized -> review
function overallRecommendation(scenario) {
  const overall = computeChainScore(scenario);
  if (overall.status === "NOT_APPLICABLE") return { rec: "na", overall };
  if (overall.hardGate) return { rec: "resurvey", overall };
  for (const p of scenario.points) { if (pointPerPointGated(p.scores)) return { rec: "review", overall }; }
  for (const p of scenario.points) { for (const i of INDICATORS) { if (severityForScore(i, p.scores[i.id]) === "material") return { rec: "review", overall }; } }
  return { rec: "good", overall };
}

// ============================================================
// RANKING — per-point findings aggregated
// ============================================================
function rankFindings(scenario) {
  const findings = [];
  for (const p of scenario.points) {
    for (const i of INDICATORS) {
      const s = p.scores[i.id];
      const band = getBandForScore(i, s);
      if (band.level === "good" || band.level === "minor") continue;
      const blockWeight = BLOCKS.find(b => b.id === i.block).weight;
      const deficit = 100 - s;
      const isGate = (i.gate_scope === "per_point_only");
      const isCritical = (band.level === "critical" || band.level === "resurvey");
      findings.push({
        indicator: i, score: s, band, point: p,
        sev: severityForScore(i, s), isHardGate: isGate, isCritical,
        priority: isGate ? 1000 + blockWeight*deficit : (isCritical ? 500 + blockWeight*deficit : blockWeight*deficit)
      });
    }
  }
  findings.sort((a,b)=>b.priority-a.priority);
  return findings;
}
function rankVerified(scenario) {
  const candidates = [];
  for (const i of INDICATORS) {
    const allPass = scenario.points.every(p => getBandForScore(i, p.scores[i.id]).level === "good");
    if (allPass) {
      const blockWeight = BLOCKS.find(b => b.id === i.block).weight;
      candidates.push({ indicator: i, priority: (i.gate_scope==="per_point_only"?1000:0) + blockWeight*i.weight*100 });
    }
  }
  candidates.sort((a,b)=>b.priority-a.priority);
  return candidates;
}
function recommendationLabel(rec) {
  return { good:"Good to go", review:"Review recommended", resurvey:"Resurvey recommended", na:"Not applicable — survey designed without check points" }[rec];
}

/* ===========================================================================
   CHECK POINT UI LAYER — per-point RTK chain, rendered into the DATUM hero structure.
   Assumes the LOCKED gcp data+engine is defined above:
     BLOCKS, INDICATOR_LIBRARY, INDICATORS, SCENARIOS, GLOBAL_GATE_CONDITION,
     getBandForScore, severityForBand, severityForScore, scoreLevel, substitutePointId,
     computePointBlockScore, computePointScore, checkChainHardGate, computeChainScore,
     overallRecommendation(scenario), rankFindings(scenario), rankVerified(scenario),
     recommendationLabel(rec)
   Check Point is a PER-POINT RTK chain: each scenario has points[], each with its own scores.
   The hero aggregates across points; the drawers list point-specific findings.
   All DOM ids are prefixed "cp-" (legacy gc- ids are replaced).
   =========================================================================== */

var REC_LABEL={good:"GOOD TO GO",review:"REVIEW",resurvey:"RESURVEY",na:"NOT APPLICABLE"};
var REC_VERDICT_COLOR={good:"rgba(16,185,214,.9)",review:"rgba(232,228,218,.94)",resurvey:"var(--red)",na:"rgba(232,228,218,.6)"};
var REC_REASON={
  good:"Every check point achieved FIXED RTK with good sigma — independent control is survey-grade.",
  review:"One or more points carry soft flags (FLOAT fix or elevated sigma) — review them before relying on the control.",
  resurvey:"Every point failed in at least one dimension (all FLOAT or all zero-sigma) — the global gate fired; re-occupy.",
  na:"This survey was designed without designated check points — the check-point confidence chain doesn't apply."
};
var SCEN_SHORT={all_fixed:"Fixed",mixed:"Mixed",all_float:"All Float",no_checkpoints:"No CP"};
var LVL_RANK={good:0,minor:1,review:2,critical:3,resurvey:3};

function blockIndicators(blockId){return INDICATORS.filter(function(i){return i.block===blockId;});}
function pctRound(n){return Math.round(n);}
function statusText(level){return level==="good"?"OK":(level==="resurvey"?"Resurvey":(level==="na"?"N/A":"Review"));}
function hasPoints(s){return !!(s&&s.points&&s.points.length);}

/* canonical aggregation: per block, mean − 0.25×(100 − min) across points, then block-weighted */
function blockPerPoint(blockId,scenario){return scenario.points.map(function(p){return computePointBlockScore(blockId,p.scores);});}
function aggBlockScore(blockId,scenario){
  if(!hasPoints(scenario))return null;
  var ps=blockPerPoint(blockId,scenario);
  var mean=ps.reduce(function(a,c){return a+c;},0)/ps.length;
  var min=Math.min.apply(null,ps);
  return mean-0.25*(100-min);
}
function aggOverallCanon(scenario){
  if(!hasPoints(scenario))return null;
  if(checkChainHardGate(scenario.points).fired)return 0;
  var s=0;BLOCKS.forEach(function(b){s+=b.weight*aggBlockScore(b.id,scenario);});
  return s;
}
/* display name — drop the "Per-point" prefix for clean, consistent block names */
function blockName(b){return (b.name||"").replace(/^Per-point\s+/i,"");}
function aggIndicatorLevel(ind,scenario){
  if(!hasPoints(scenario))return "good";
  var worst="good";
  scenario.points.forEach(function(p){
    var lvl=getBandForScore(ind,p.scores[ind.id]).level;
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst==="critical"?"resurvey":worst;
}
function aggIndicatorWorstScore(ind,scenario){
  if(!hasPoints(scenario))return null;
  var mn=101;scenario.points.forEach(function(p){var s=p.scores[ind.id];if(s<mn)mn=s;});
  return mn===101?null:mn;
}
function worstPointFor(ind,scenario){
  var wp=null,mn=101;
  (scenario.points||[]).forEach(function(p){var s=p.scores[ind.id];if(s<mn){mn=s;wp=p;}});
  return wp;
}
function aggBlockLevel(blockId,scenario){
  if(!hasPoints(scenario))return "na";
  var worst="good";
  blockIndicators(blockId).forEach(function(ind){
    var lvl=aggIndicatorLevel(ind,scenario);
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst;
}
function importance(ind){
  var b=BLOCKS.filter(function(x){return x.id===ind.block;})[0];var bw=b?b.weight:0;
  return (ind.is_critical_path?1000:0)+bw*ind.weight*100;
}
/* rankFindings excludes minor — gather minor (hygiene) findings separately for "Noted" */
function minorFindings(scenario){
  var out=[];
  (scenario.points||[]).forEach(function(p){
    INDICATORS.forEach(function(i){
      var band=getBandForScore(i,p.scores[i.id]);
      if(band.level==="minor")out.push({indicator:i,point:p,band:band,score:p.scores[i.id]});
    });
  });
  return out;
}
function scenRec(s){return overallRecommendation(s).rec;}

/* per-point overall severity (for the roster chips) */
function pointLevel(p){
  var worst="good";
  INDICATORS.forEach(function(i){
    var lvl=getBandForScore(i,p.scores[i.id]).level;
    if(LVL_RANK[lvl]>LVL_RANK[worst])worst=lvl;
  });
  return worst==="minor"?"good":worst;
}
function renderRoster(){
  var host=document.getElementById("cp-roster"),sum=document.getElementById("cp-rosterSummary");
  if(!host)return;
  var sc=currentScenario;
  if(!hasPoints(sc)){
    host.innerHTML='<div class="cp-roster-empty">No check points were designated for this survey — independent accuracy validation comes from another control strategy.</div>';
    if(sum)sum.textContent="0 points";return;
  }
  var ok=0,flag=0;
  host.innerHTML=sc.points.map(function(p){
    var lvl=pointLevel(p); if(lvl==="good")ok++;else flag++;
    var ps=pctRound(computePointScore(p));
    var on=currentPoint===p.id?" selected":"";
    return '<div class="cp-pt '+lvl+on+'" onclick="dsCp.selectPoint(\''+p.id+'\')" title="'+p.id+' · '+statusText(lvl)+' · '+ps+'%">'+
        '<div class="cp-pt-top"><span class="cp-pt-dot"></span><span class="cp-pt-id">'+p.id+'</span></div>'+
        '<div class="cp-pt-dev">'+(p.device_type||"")+' · '+ps+'%</div>'+
      '</div>';
  }).join("");
  if(sum)sum.textContent=sc.points.length+" check points · "+ok+" OK · "+flag+" flagged";
}

/* illustrative trend (sample); last point ≈ the real Mixed-Quality score */
var TREND=[
  {sid:"S-061",date:"Oct 25",score:93,anom:false},
  {sid:"S-062",date:"Nov 25",score:95,anom:false},
  {sid:"S-063",date:"Nov 25",score:88,anom:true,note:"Two points high multipath"},
  {sid:"S-064",date:"Dec 25",score:94,anom:false},
  {sid:"S-065",date:"Dec 25",score:97,anom:false},
  {sid:"S-066",date:"Jan 26",score:96,anom:false},
  {sid:"S-067",date:"Jan 26",score:95,anom:false},
  {sid:"S-068",date:"Feb 26",score:97,anom:false},
  {sid:"S-069",date:"Mar 26",score:98,anom:false},
  {sid:"S-070",date:"May 26",score:58,anom:true,note:"CP-002 FLOAT, CP-003 high sigma"}
];
var FLEET=[90,92,89,93,95,94,93,95,96,95];
var fleetOn=false;

var POS={
  1:[[50,16]],
  2:[[26,30],[74,30]],
  3:[[24,40],[76,40],[50,84]],
  4:[[22,32],[78,32],[28,76],[72,76]],
  5:[[50,12],[20,34],[80,34],[30,82],[70,82]],
  6:[[22,20],[78,20],[14,52],[86,52],[34,84],[66,84]],
  7:[[50,10],[20,26],[80,26],[12,56],[88,56],[34,86],[66,86]]
};
function posFor(n){
  if(POS[n])return POS[n];
  var a=[];for(var i=0;i<n;i++){var ang=(i/n)*2*Math.PI-Math.PI/2;a.push([50+38*Math.cos(ang),50+40*Math.sin(ang)]);}
  return a;
}

var currentScenario=SCENARIOS.filter(function(s){return s.id==="mixed";})[0]||SCENARIOS[0];
var currentPoint=null;   /* null = Overall (aggregate); else a point id = single-point view */
var selected={};

/* point-scoped recommendation copy (single-point view) */
var POINT_REASON={
  good:"This check point achieved FIXED RTK with good sigma — survey-grade at this point.",
  review:"This check point has soft flags — review them before relying on it.",
  resurvey:"This check point is FLOAT/AUTONOMOUS or undocumented (per-point gate) — re-occupy this point."
};
function curPoint(){return currentPoint?((currentScenario.points||[]).filter(function(p){return p.id===currentPoint;})[0]||null):null;}
function pointRec(p){
  var worst="good";
  INDICATORS.forEach(function(ind){
    var sev=severityForScore(ind,p.scores[ind.id]);
    if(sev==="critical")worst="resurvey";
    else if(sev==="material"&&worst!=="resurvey")worst="review";
  });
  return {rec:worst,score:computePointScore(p)};
}
function pointBlockLevel(blockId,p){
  var worst="good";
  blockIndicators(blockId).forEach(function(ind){
    var lvl=getBandForScore(ind,p.scores[ind.id]).level;
    if(lvl==="resurvey"||lvl==="critical")worst="resurvey";
    else if(lvl==="review"&&worst!=="resurvey")worst="review";
  });
  return worst;
}
function renderPointSelect(){
  var el=document.getElementById("cp-pointSelect");if(!el)return;
  if(!hasPoints(currentScenario)){el.innerHTML='<option value="overall" selected>Overall</option>';el.disabled=true;return;}
  el.disabled=false;
  var o='<option value="overall"'+(currentPoint?'':' selected')+'>Overall</option>';
  currentScenario.points.forEach(function(p){
    o+='<option value="'+p.id+'"'+(currentPoint===p.id?' selected':'')+'>'+p.id+(p.device_type?' · '+p.device_type:'')+'</option>';
  });
  el.innerHTML=o;
}
function selectPoint(val){currentPoint=(val&&val!=="overall")?val:null;selected={};closeDrawer();renderAll();}

/* ---- sparkline ---- */
(function(){
  var svg=document.getElementById("cp-sparkSvg");if(!svg)return;
  var W=192,H=52,pL=2,pR=2,pT=5,pB=5,n=TREND.length,mn=50,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*(W-pL-pR):(W-pL-pR)/2)};
  var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*(H-pT-pB)};
  var area="M "+pL+" "+(H-pB);TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});area+=" L "+(W-pR)+" "+(H-pB)+" Z";
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});
  var s='<path fill="url(#cp-spGrad)" d="'+area+'"/><path fill="none" stroke="rgba(16,185,214,.5)" stroke-width="1" d="'+line+'"/>';
  TREND.forEach(function(d,i){s+='<circle fill="'+(d.anom?"rgba(232,228,218,.4)":"rgba(16,185,214,.5)")+'" cx="'+sx(i)+'" cy="'+sy(d.score)+'" r="1.8"><title>'+d.sid+" · "+d.score+'</title></circle>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1" cx="'+lx+'" cy="'+ly+'" r="3.8"/>';
  svg.innerHTML+=s;
})();
function openTrend(){document.getElementById("cp-trendModal").classList.add("open");drawTrend();}
function closeTrend(){document.getElementById("cp-trendModal").classList.remove("open");}
function toggleFleet(){fleetOn=!fleetOn;document.getElementById("cp-fleetBtn").classList.toggle("on",fleetOn);drawTrend();}
function drawTrend(){
  var svg=document.getElementById("cp-trendSvg");var W=900,H=256,pL=44,pR=24,pT=14,pB=34,iW=W-pL-pR,iH=H-pT-pB,n=TREND.length,mn=40,mx=100;
  var sx=function(i){return pL+(n>1?i/(n-1)*iW:iW/2)};var sy=function(s){return pT+(1-(s-mn)/(mx-mn))*iH};
  var s='<defs><linearGradient id="cp-tgGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(16,185,214,.25)"/><stop offset="100%" stop-color="rgba(16,185,214,.00)"/></linearGradient></defs>';
  [40,60,75,90,100].forEach(function(v){s+='<line class="tg-axis" x1="'+pL+'" y1="'+sy(v)+'" x2="'+(W-pR)+'" y2="'+sy(v)+'"/><text class="tg-tick" x="'+(pL-6)+'" y="'+(sy(v)+3)+'" text-anchor="end">'+v+'</text>';});
  s+='<rect class="tg-band" x="'+pL+'" y="'+sy(100)+'" width="'+iW+'" height="'+(sy(90)-sy(100))+'"/>';
  var area="M "+pL+" "+sy(mn);TREND.forEach(function(d,i){area+=" L "+sx(i)+" "+sy(d.score)});area+=" L "+(W-pR)+" "+sy(mn)+" Z";
  s+='<path d="'+area+'" fill="url(#cp-tgGrad)"/>';
  if(fleetOn){var fp="";FLEET.forEach(function(v,i){fp+=(i===0?"M ":"L ")+sx(i)+" "+sy(v)+" "});s+='<path class="tg-fleet" d="'+fp+'"/><text class="tg-lbl" x="'+(sx(FLEET.length-1)+5)+'" y="'+(sy(FLEET[FLEET.length-1])+3)+'">fleet median</text>';}
  var line="";TREND.forEach(function(d,i){line+=(i===0?"M ":"L ")+sx(i)+" "+sy(d.score)+" "});s+='<path class="tg-line" d="'+line+'"/>';
  TREND.forEach(function(d,i){var x=sx(i),y=sy(d.score);s+='<circle class="tg-pt'+(d.anom?" anom":"")+'" cx="'+x+'" cy="'+y+'" r="4.5"><title>'+d.sid+" · "+d.score+(d.note?" ("+d.note+")":"")+'</title></circle>';if(i%2===0||i===n-1)s+='<text class="tg-tick" x="'+x+'" y="'+(H-pB+13)+'" text-anchor="middle">'+d.date+'</text>';});
  var lx=sx(n-1),ly=sy(TREND[n-1].score);
  s+='<circle cx="'+lx+'" cy="'+ly+'" r="7" fill="none" stroke="rgba(16,185,214,.3)" stroke-width="1"/><text class="tg-lbl" x="'+(lx-7)+'" y="'+(ly-11)+'" text-anchor="end" fill="rgba(16,185,214,.6)">current · '+TREND[n-1].score+'</text>';
  svg.innerHTML=s;
}
function toggleBBSection(){document.getElementById("cp-bbSectionBody").classList.toggle("open");document.getElementById("cp-bbSectionIcon").classList.toggle("open");}

/* ---- picker ---- */
function renderScenarioPicker(){
  var el=document.getElementById("cp-scnPick");if(!el)return;
  el.innerHTML=SCENARIOS.map(function(s){
    var on=s.id===currentScenario.id,rec=scenRec(s);
    var cls="scn-opt"+(on?" on":"");
    if(on&&(rec==="review"||rec==="na"))cls+=" warn";
    if(on&&rec==="resurvey")cls+=" bad";
    return '<button class="'+cls+'" onclick="dsCp.selectScenario(\''+s.id+'\')">'+(SCEN_SHORT[s.id]||s.name)+'</button>';
  }).join("");
}
function selectScenario(id){var s=SCENARIOS.filter(function(x){return x.id===id;})[0];if(!s)return;currentScenario=s;currentPoint=null;selected={};closeDrawer();renderAll();}

/* ---- headline ---- */
function renderHeadline(){
  var p=curPoint();
  if(p){
    var pr=pointRec(p);
    document.getElementById("cp-scoreNum").innerHTML=pctRound(pr.score)+'<span class="pct">%</span>';
    document.getElementById("cp-scoreDelta").textContent=p.id+(p.device_type?" · "+p.device_type:"")+" · single point";
    var vt0=document.getElementById("cp-mdVerdictText");if(vt0)vt0.textContent=REC_LABEL[pr.rec];
    var v0=document.getElementById("cp-mdVerdict");if(v0)v0.style.color=REC_VERDICT_COLOR[pr.rec];
    document.getElementById("cp-mdReason").innerHTML=POINT_REASON[pr.rec];
    return;
  }
  var rec=overallRecommendation(currentScenario),ov=rec.overall;
  var numEl=document.getElementById("cp-scoreNum"),dEl=document.getElementById("cp-scoreDelta");
  if(ov.status==="NOT_APPLICABLE"){
    numEl.innerHTML='N/A';
    dEl.textContent="Survey designed without check points";
  }else if(ov.hardGate){
    numEl.innerHTML='0<span class="pct">%</span>';
    dEl.textContent="Global gate — every point FLOAT or zero-sigma";
  }else{
    numEl.innerHTML=pctRound(aggOverallCanon(currentScenario))+'<span class="pct">%</span>';
    dEl.textContent="Across "+currentScenario.points.length+" check points · "+BLOCKS.length+" blocks";
  }
  var vt=document.getElementById("cp-mdVerdictText");if(vt)vt.textContent=REC_LABEL[rec.rec];
  var verdict=document.getElementById("cp-mdVerdict");if(verdict)verdict.style.color=REC_VERDICT_COLOR[rec.rec];
  document.getElementById("cp-mdReason").innerHTML=REC_REASON[rec.rec];
}

/* ---- BB cards ---- */
function renderBBCards(){
  var sc=currentScenario,p=curPoint();
  var host=document.getElementById("cp-bbStripHead");
  host.innerHTML=BLOCKS.map(function(b,idx){
    var raw,lvl;
    if(p){ raw=computePointBlockScore(b.id,p.scores); lvl=pointBlockLevel(b.id,p); }
    else { raw=aggBlockScore(b.id,sc); lvl=raw===null?"na":aggBlockLevel(b.id,sc); }
    var bs=raw===null?"N/A":pctRound(raw)+"%";
    var cls="bb-card"+(lvl==="review"?" review":"")+(lvl==="resurvey"?" resurvey":"");
    var fillW=raw===null?0:(lvl==="good"?100:pctRound(raw));
    var fillCol=lvl==="good"?"rgba(16,185,214,.38)":(lvl==="resurvey"?"rgba(201,64,64,.5)":(lvl==="na"?"rgba(255,255,255,.06)":"rgba(232,228,218,.18)"));
    return '<div class="'+cls+'" id="cp-'+b.id+'">'+
      '<div class="bb-header"><div class="bb-h-left">'+
        '<div class="bb-num">BB · 0'+(idx+1)+'</div>'+
        '<div class="bb-name">'+blockName(b)+'</div>'+
        '<div class="bb-weight">weight '+b.weight.toFixed(2)+'</div>'+
      '</div><div class="bb-h-right">'+
        '<div class="bb-score-sm">'+bs+'</div><div class="bb-status-dot"></div>'+
      '</div></div>'+
      '<div class="bb-inner-always">'+
        '<div class="bb-bar"><div class="bb-bar-fill" style="width:'+fillW+'%;background:'+fillCol+'"></div></div>'+
        '<div class="bb-toggle-row" onclick="dsCp.toggleBBIndicators(\''+b.id+'\')"><span class="bb-check"></span><span class="bb-toggle-text">Show indicators</span></div>'+
        '<div class="bb-status-full">'+statusText(lvl)+'</div>'+
        '<div class="bb-details" onclick="event.stopPropagation();dsCp.openBBDetails(\''+b.id+'\')">Details ›</div>'+
      '</div></div>';
  }).join("");
  markActiveBB();
}

/* ---- pills (one per indicator, aggregate severity across points) ---- */
function renderIndicators(){
  var layer=document.getElementById("cp-indicatorLayer");if(!layer)return;
  var sc=currentScenario,p=curPoint(),html=[];
  if(p){
    BLOCKS.forEach(function(b){
      if(!selected[b.id])return;
      var inds=blockIndicators(b.id),pts=posFor(inds.length);
      inds.forEach(function(ind,i){
        var pos=pts[i]||[50,75];
        var s=p.scores[ind.id],lvl=getBandForScore(ind,s).level;
        var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
        html.push('<div class="indicator-pill'+sev+'" style="left:'+pos[0]+'%;top:'+pos[1]+'%" title="'+ind.name+' · '+s+'"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+s+'</b></div>');
      });
    });
    layer.innerHTML=html.join("");
    layer.className="indicator-layer"+(html.length?" show":"");
    return;
  }
  if(hasPoints(sc)){
    BLOCKS.forEach(function(b){
      if(!selected[b.id])return;
      var inds=blockIndicators(b.id),pts=posFor(inds.length);
      inds.forEach(function(ind,i){
        var p=pts[i]||[50,75];
        var lvl=aggIndicatorLevel(ind,sc);
        var sev=lvl==="good"?"":(lvl==="resurvey"?" sev-resurvey":(lvl==="minor"?" sev-minor":" sev-review"));
        var ws=aggIndicatorWorstScore(ind,sc);
        var wp=(lvl!=="good")?worstPointFor(ind,sc):null;
        var ptTag=wp?'<i class="ip-pt">'+wp.id+'</i>':'';
        var tip=ind.name+' · worst '+(ws===null?"—":ws)+(wp?' at '+wp.id:'');
        html.push('<div class="indicator-pill'+sev+'" style="left:'+p[0]+'%;top:'+p[1]+'%" title="'+tip+'"><span></span>'+ind.name.toUpperCase()+'<b class="ip-score">'+(ws===null?"—":ws)+ptTag+'</b></div>');
      });
    });
  }
  layer.innerHTML=html.join("");
  layer.className="indicator-layer"+(html.length?" show":"");
}
function toggleBBIndicators(id){selected[id]=!selected[id];markActiveBB();renderIndicators();}
function markActiveBB(){BLOCKS.forEach(function(b){var el=document.getElementById("cp-"+b.id);if(el)el.classList.toggle("active",!!selected[b.id]);});}

/* ---- per-block Details drawer (aggregate decomposition, worst-point detail) ---- */
function openBBDetails(blockId){
  selected[blockId]=true;markActiveBB();renderIndicators();
  var b=BLOCKS.filter(function(x){return x.id===blockId;})[0];
  var sc=currentScenario;
  var p=curPoint();
  if(p){
    var pbs=pctRound(computePointBlockScore(blockId,p.scores)),plvl=pointBlockLevel(blockId,p);
    var prows=blockIndicators(blockId).map(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s);
      var sevCls=band.level==="good"?"":((band.level==="resurvey"||band.level==="critical")?"resurvey":(band.level==="minor"?"":"review"));
      var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
      var h='<div class="d-ind"><div class="d-ind-top">'+
        '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
        '<div class="d-ind-sc '+sevCls+'">'+s+'</div></div>'+
        '<div class="d-ind-band">'+(band.level==="good"?substitutePointId(ind.verified_statement,p.id):substitutePointId(band.label,p.id))+'</div>';
      if(band.impact)h+='<div class="d-ind-impact">'+substitutePointId(band.impact,p.id)+'</div>';
      if(band.actions)h+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,p.id)+'</li>';}).join("")+'</ul>';
      h+='<div class="d-deriv">in-block weight '+ind.weight.toFixed(2)+' · contributes '+Math.round(ind.weight*s)+'/'+Math.round(ind.weight*100)+'</div>';
      if(ind.derivation)h+='<div class="d-deriv">'+ind.derivation+'</div>';
      return h+'</div>';
    }).join("");
    var plim=null,plimv=1e9;
    blockIndicators(blockId).forEach(function(ind){var v=ind.weight*p.scores[ind.id];if(v<plimv){plimv=v;plim=ind;}});
    var plimHtml=plim&&plvl!=="good"?'<div class="d-gate" style="color:rgba(232,228,218,.6);border-color:var(--line);background:rgba(255,255,255,.014)">Limiting factor: '+plim.name+'</div>':'';
    document.getElementById("cp-drawerBody").innerHTML=
      '<h2>'+blockName(b)+'</h2>'+
      '<div class="d-score">'+pbs+'<span>%</span></div>'+
      '<div class="d-verdict '+plvl+'">'+statusText(plvl)+'  ·  '+p.id+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
      '<div class="d-narr">'+b.description+'</div>'+plimHtml+
      '<div class="d-sec">Indicators</div>'+prows;
    openDrawer();return;
  }
  var raw=aggBlockScore(blockId,sc);
  var lvl=raw===null?"na":aggBlockLevel(blockId,sc);
  var inds=blockIndicators(blockId);
  var rows=inds.map(function(ind){
    var lev=aggIndicatorLevel(ind,sc);
    var ws=aggIndicatorWorstScore(ind,sc);
    var wp=worstPointFor(ind,sc);
    var pid=wp?wp.id:"";
    var band=wp?getBandForScore(ind,ws):null;
    var sevCls=lev==="good"?"":((lev==="resurvey"||lev==="critical")?"resurvey":(lev==="minor"?"":"review"));
    var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
    var html='<div class="d-ind"><div class="d-ind-top">'+
      '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
      '<div class="d-ind-sc '+sevCls+'">'+(ws===null?"—":ws)+'</div></div>'+
      '<div class="d-ind-band">'+(lev==="good"?substitutePointId(ind.verified_statement,"every point"):substitutePointId(band.label,pid))+'</div>';
    if(band&&band.impact)html+='<div class="d-ind-impact">'+substitutePointId(band.impact,pid)+'</div>';
    if(band&&band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,pid)+'</li>';}).join("")+'</ul>';
    var contrib=ws===null?0:(ind.weight*ws);
    html+='<div class="d-deriv">in-block weight '+ind.weight.toFixed(2)+' · contributes '+(ws===null?"—":Math.round(contrib)+'/'+Math.round(ind.weight*100))+(lev!=="good"&&pid?' · worst at '+pid:"")+'</div>';
    if(ind.derivation)html+='<div class="d-deriv">'+ind.derivation+'</div>';
    return html+'</div>';
  }).join("");
  var lim=null,limv=1e9;
  inds.forEach(function(ind){var ws=aggIndicatorWorstScore(ind,sc);if(ws===null)return;var v=ind.weight*ws;if(v<limv){limv=v;lim=ind;}});
  var limHtml=lim&&lvl!=="good"&&lvl!=="na"?'<div class="d-gate" style="color:rgba(232,228,218,.6);border-color:var(--line);background:rgba(255,255,255,.014)">Limiting factor: '+lim.name+'</div>':'';
  var formHtml="";
  if(raw!==null){
    var ps2=blockPerPoint(blockId,sc),mean2=Math.round(ps2.reduce(function(a,c){return a+c;},0)/ps2.length),min2=Math.min.apply(null,ps2),pen2=Math.round(0.25*(100-min2)),wpId="",lo=1e9;
    sc.points.forEach(function(pp){var v=computePointBlockScore(blockId,pp.scores);if(v<lo){lo=v;wpId=pp.id;}});
    formHtml='<div class="d-deriv" style="margin:2px 0 12px">Aggregate = mean '+mean2+' across '+ps2.length+' points − '+pen2+' spread penalty (worst point '+wpId+' at '+Math.round(min2)+'). Select that point above for fix-level detail.</div>';
  }
  document.getElementById("cp-drawerBody").innerHTML=
    '<h2>'+blockName(b)+'</h2>'+
    '<div class="d-score">'+(raw===null?"N/A":pctRound(raw)+'<span>%</span>')+'</div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  block weight '+b.weight.toFixed(2)+'</div>'+
    '<div class="d-narr">'+b.description+'</div>'+formHtml+limHtml+
    '<div class="d-sec">Indicators · worst across points</div>'+rows;
  openDrawer();
}

/* ---- per-POINT Details drawer (drill into a single check point) ---- */
function openPointDetails(pointId){
  var sc=currentScenario;
  var p=(sc.points||[]).filter(function(x){return x.id===pointId;})[0];if(!p)return;
  var ps=pctRound(computePointScore(p)),lvl=pointLevel(p);
  var blocksHtml=BLOCKS.map(function(b){
    var bs=pctRound(computePointBlockScore(b.id,p.scores));
    var rows=blockIndicators(b.id).map(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s);
      var sevCls=band.level==="good"?"":((band.level==="resurvey"||band.level==="critical")?"resurvey":(band.level==="minor"?"":"review"));
      var gate=ind.is_critical_path?' <span class="acc-tag resurvey" style="margin-left:6px">Hard gate</span>':'';
      var html='<div class="d-ind"><div class="d-ind-top">'+
        '<div class="d-ind-name">'+ind.num+'  '+ind.name+gate+'</div>'+
        '<div class="d-ind-sc '+sevCls+'">'+s+'</div></div>'+
        '<div class="d-ind-band">'+(band.level==="good"?substitutePointId(ind.verified_statement,p.id):substitutePointId(band.label,p.id))+'</div>';
      if(band.impact)html+='<div class="d-ind-impact">'+substitutePointId(band.impact,p.id)+'</div>';
      if(band.actions)html+='<ul class="d-acts">'+band.actions.map(function(a){return'<li>'+substitutePointId(a,p.id)+'</li>';}).join("")+'</ul>';
      return html+'</div>';
    }).join("");
    return '<div class="d-sec">'+blockName(b)+'  ·  '+bs+'%</div>'+rows;
  }).join("");
  document.getElementById("cp-drawerBody").innerHTML=
    '<h2>'+p.id+'</h2>'+
    '<div class="d-score">'+ps+'<span>%</span></div>'+
    '<div class="d-verdict '+lvl+'">'+statusText(lvl)+'  ·  '+(p.device_type||"")+' device</div>'+
    '<div class="d-narr">Per-point breakdown for '+p.id+' across all '+BLOCKS.length+' blocks. This point contributes to the averaged chain score.</div>'+
    blocksHtml;
  openDrawer();
}

/* ---- Why drawer (per-point findings) ---- */
function accFinding(f,kind,open){
  var sevCls=kind==="resurvey"?"resurvey":(kind==="review"?"review":"");
  var tag={resurvey:"Resurvey",review:"Review",noted:"Noted"}[kind];
  var pid=f.point?f.point.id:"";
  var body='<div class="acc-state">'+substitutePointId(f.band.label,pid)+'</div>'+
    (f.band.impact?'<div class="d-ind-impact">'+substitutePointId(f.band.impact,pid)+'</div>':'')+
    (f.band.actions?'<ul class="d-acts">'+f.band.actions.map(function(a){return'<li>'+substitutePointId(a,pid)+'</li>';}).join("")+'</ul>':'');
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+f.indicator.name+
      (pid?' <span style="color:rgba(232,228,218,.5)">· '+pid+'</span>':"")+'</span>'+
    '<span class="acc-right"><span class="acc-sc '+sevCls+'">'+f.score+'</span></span>'+
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function accVerified(ind,scenario,open){
  var n=scenario.points.length;
  var body='<div class="acc-state">'+substitutePointId(ind.verified_statement,"every point")+'</div>'+
    '<div class="acc-evi">Evidence · good across all '+n+' point'+(n===1?"":"s")+'</div>';
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+ind.name+'</span>'+
    
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function setSection(sel,open){var rows=document.querySelectorAll(sel+" .acc");for(var i=0;i<rows.length;i++)rows[i].classList.toggle("open",open);}

function accPointVerified(ind,band,p,open){
  var body='<div class="acc-state">'+substitutePointId(ind.verified_statement,p.id)+'</div>'+
    '<div class="acc-evi">Evidence · '+band.label+'</div>';
  return '<div class="acc'+(open?" open":"")+'"><div class="acc-head" onclick="this.parentNode.classList.toggle(\'open\')">'+
    '<span class="acc-chev">▶</span><span class="acc-name">'+ind.name+'</span>'+
    '<span class="acc-right"><span class="acc-sc">'+p.scores[ind.id]+'</span></span>'+
    '</div><div class="acc-body"><div class="acc-inner">'+body+'</div></div></div>';
}
function verifiedBlock(count,listHtml){
  var head='<div class="d-sec-row"><div class="d-sec verified">Verified<span class="d-sec-count">'+count+'</span></div></div>';
  if(count<=0) return head+'<div id="cp-verSec">'+listHtml+'</div>';
  var summary=(typeof INDICATORS!=='undefined'&&count===INDICATORS.length)?('All '+count+' indicators passed verification.'):(count+' indicators verified and in good standing.');
  return '<div class="d-sec-row"><div style="display:flex;align-items:baseline;gap:10px;flex:1;min-width:0"><span class="d-sec verified" style="margin:0;padding:0;border:0;flex-shrink:0">Verified</span><span class="d-empty" style="padding:0">'+summary+'</span></div><button class="d-ctrl" id="cp-verToggle" onclick="dsCp.toggleVerified()" style="flex-shrink:0">+ More Details</button></div>'+'<div id="cp-verSec" style="display:none">'+listHtml+'</div>';
}
function toggleVerified(){
  var sec=document.getElementById('cp-verSec'),tog=document.getElementById('cp-verToggle');
  if(!sec||!tog)return;
  var open=(sec.style.display==='none');
  sec.style.display=open?'block':'none';
  tog.innerHTML=open?'\u2212 Show less':'+ More Details';
}
function openRecommendation(){
  var sc=currentScenario,p=curPoint();
  if(p){
    var pr=pointRec(p),act=[],ver=[],noted=[];
    INDICATORS.forEach(function(ind){
      var s=p.scores[ind.id],band=getBandForScore(ind,s),sev=severityForScore(ind,s);
      if(sev==="critical")act.push({indicator:ind,score:s,band:band,point:p,kind:"resurvey",rank:0});
      else if(sev==="material")act.push({indicator:ind,score:s,band:band,point:p,kind:"review",rank:1});
      else if(sev==="minor")noted.push({indicator:ind,score:s,band:band,point:p});
      else ver.push({indicator:ind,band:band});
    });
    act.sort(function(a,b){return a.rank-b.rank||importance(b.indicator)-importance(a.indicator);});
    ver.sort(function(a,b){return importance(b.indicator)-importance(a.indicator);});
    var vOpen=act.length===0;
    var actH=act.length?act.map(function(f){return accFinding(f,f.kind,true);}).join(""):'<div class="d-empty">Nothing to action — this point has no Review or Resurvey findings.</div>';
    var notedH=noted.map(function(f){return accFinding(f,"noted",false);}).join("");
    var verH=ver.length?ver.map(function(f){return accPointVerified(f.indicator,f.band,p,false);}).join(""):'<div class="d-empty">No indicator passed cleanly at this point.</div>';
    document.getElementById("cp-drawerBody").innerHTML=
      '<h2>Why '+(REC_LABEL[pr.rec]==="GOOD TO GO"?"Good to go":REC_LABEL[pr.rec])+'?</h2>'+
      
      
      '<div class="d-narr">'+POINT_REASON[pr.rec]+'</div>'+
      '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+act.length+'</span></div>'+
        '<div class="d-ctrls"><button class="d-ctrl" onclick="dsCp.setSection(\'#cp-actSec\',true)">Expand all</button>'+
        '<button class="d-ctrl" onclick="dsCp.setSection(\'#cp-actSec\',false)">Collapse all</button></div></div>'+
      '<div id="cp-actSec">'+actH+notedH+'</div>'+
      verifiedBlock(ver.length, verH);
    openDrawer();return;
  }
  var rec=overallRecommendation(sc),ov=rec.overall;
  if(ov.status==="NOT_APPLICABLE"){
    document.getElementById("cp-drawerBody").innerHTML=
      '<h2>Why Not applicable?</h2>'+
      
      '<div class="d-narr">'+REC_REASON.na+'</div>'+
      '<div class="d-empty">No check points were designated for this survey, so there is nothing to score. Independent accuracy validation comes from another control strategy instead.</div>';
    openDrawer();return;
  }
  var findings=rankFindings(sc);                 // review + resurvey, per point, pre-sorted
  var noted=minorFindings(sc);                   // minor/hygiene, per point
  var verified=rankVerified(sc);                 // indicators passing all points
  var gateHtml=ov.hardGate?'<div class="d-gate">HARD GATE — '+GLOBAL_GATE_CONDITION+'</div>':'';
  var verifiedOpen=findings.length===0;
  var actHtml=findings.length
    ? findings.map(function(f){return accFinding(f,(f.band.level==="resurvey"||f.band.level==="critical")?"resurvey":"review",true);}).join("")
    : '<div class="d-empty">Nothing to action — no point has a Review or Resurvey finding.</div>';
  var notedHtml=noted.map(function(f){return accFinding(f,"noted",false);}).join("");
  var verHtml=verified.length
    ? verified.map(function(c){return accVerified(c.indicator,sc,false);}).join("")
    : '<div class="d-empty">No indicator passed cleanly across all points.</div>';
  document.getElementById("cp-drawerBody").innerHTML=
    '<h2>Why '+(REC_LABEL[rec.rec]==="GOOD TO GO"?"Good to go":REC_LABEL[rec.rec])+'?</h2>'+
    
    
    '<div class="d-narr">'+REC_REASON[rec.rec]+'</div>'+gateHtml+
    '<div class="d-sec-row"><div class="d-sec actionable">Actionables<span class="d-sec-count">'+findings.length+'</span></div>'+
      '<div class="d-ctrls"><button class="d-ctrl" onclick="dsCp.setSection(\'#cp-actSec\',true)">Expand all</button>'+
      '<button class="d-ctrl" onclick="dsCp.setSection(\'#cp-actSec\',false)">Collapse all</button></div></div>'+
    '<div id="cp-actSec">'+actHtml+notedHtml+'</div>'+
    verifiedBlock(verified.length, verHtml);
  openDrawer();
}

function openDrawer(){document.getElementById("cp-drawer").classList.add("open");}
function closeDrawer(){document.getElementById("cp-drawer").classList.remove("open");}
var CHECK_POINT_API_READY=false;
function renderCheckPointNoApi(msg){
  var score=document.getElementById("cp-scoreNum"); if(score) score.innerHTML='<span style="font-size:28px;opacity:.45;letter-spacing:.1em">NO API DATA</span>';
  var delta=document.getElementById("cp-scoreDelta"); if(delta) delta.textContent=msg||"Start the API and refresh the database.";
  var reason=document.getElementById("cp-mdReason"); if(reason) reason.textContent=msg||"No Check Point API data loaded.";
  var pick=document.getElementById("cp-scnPick"); if(pick) pick.innerHTML="";
  var points=document.getElementById("cp-pointSelect"); if(points) points.innerHTML='<option>No API data</option>';
  var cards=document.getElementById("cp-bbStripHead"); if(cards) cards.innerHTML='<div class="d-empty">No Check Point records returned by the API.</div>';
  var layer=document.getElementById("cp-indicatorLayer"); if(layer){layer.innerHTML="";layer.className="indicator-layer";}
}
function renderAll(){
  if(!CHECK_POINT_API_READY){renderCheckPointNoApi();return;}
  renderScenarioPicker();renderPointSelect();renderHeadline();renderBBCards();renderIndicators();
}

var REAL_OVERALL=(function(){var v=aggOverallCanon(currentScenario);return v==null?0:v;})();
window.dsCp={openTrend:openTrend,closeTrend:closeTrend,toggleFleet:toggleFleet,
  toggleBBSection:toggleBBSection,selectScenario:selectScenario,selectPoint:selectPoint,
  toggleBBIndicators:toggleBBIndicators,openBBDetails:openBBDetails,openPointDetails:openPointDetails,
  openRecommendation:openRecommendation,closeDrawer:closeDrawer,
  setSection:setSection,toggleVerified:toggleVerified,render:renderAll,
  refreshApi:function(){ if(!CHECK_POINT_API_READY) loadLiveCheckPointScores(); },
  realScore:REAL_OVERALL};

var CHECK_POINT_API_URL = loopApiUrl("/api/check-point/indicators");
var CHECK_POINT_API_RETRY_COUNT=0;
var CHECK_POINT_API_RETRY_MAX=240;
var CHECK_POINT_API_RETRY_MS=3000;
var CHECK_POINT_API_LOADING=false;

function cpShowLoadingState(){
  var el=document.getElementById("cp-scoreNum");
  if(el) el.innerHTML='<span style="font-size:28px;opacity:.4;letter-spacing:.1em">LOADING</span>';
}

function cpShowErrorBadge(msg){
  var badge=document.createElement("div");
  badge.style.cssText=[
    "position:fixed;bottom:18px;left:50%;transform:translateX(-50%)",
    "background:rgba(201,64,64,.18);border:1px solid rgba(201,64,64,.4)",
    "color:rgba(232,228,218,.7);font-family:var(--fm);font-size:10px",
    "letter-spacing:.12em;padding:6px 14px;border-radius:2px;z-index:9999",
    "pointer-events:none"
  ].join(";");
  badge.textContent="CHECK POINT API UNAVAILABLE - no live data loaded  ·  "+msg;
  document.body.appendChild(badge);
  setTimeout(function(){ badge.remove(); },6000);
}

function mapApiCheckPoint(apiPoint){
  var scores={},liveInputs={};
  var traces=apiPoint.indicator_traces||{};
  Object.keys(traces).forEach(function(key){
    var t=traces[key]||{};
    var id=t.indicator_id || key.split("_").slice(0,3).join("_");
    scores[id]=t.score;
    liveInputs[id]=t.input_values||{};
  });
  return {
    id:apiPoint.point_id || ("CP-"+Math.random().toString(36).slice(2,7)),
    device_type:apiPoint.device_type||"",
    scores:scores,
    _liveInputs:liveInputs
  };
}

function injectLiveCheckPointScenario(apiPoints){
  var points=apiPoints.map(mapApiCheckPoint);
  var liveScenario={id:"live",name:"Live",desc:"Live data from /api/check-point/indicators",points:points,_live:true};
  SCENARIOS.splice(0, SCENARIOS.length, liveScenario);
  currentScenario=liveScenario;
  CHECK_POINT_API_READY=true;
  currentPoint=null;
  selected={};
  closeDrawer();
}

function loadLiveCheckPointScores(){
  if(CHECK_POINT_API_LOADING) return;
  CHECK_POINT_API_LOADING=true;
  cpShowLoadingState();
  fetch(withCacheBust(CHECK_POINT_API_URL),{cache:'no-store'})
    .then(function(res){
      if(!res.ok) throw new Error("HTTP "+res.status);
      return res.json();
    })
    .then(function(data){
      CHECK_POINT_API_LOADING=false;
      var points=Array.isArray(data)?data:(data.points||[]);
      if(!Array.isArray(points)||!points.length) throw new Error("empty points array");
      injectLiveCheckPointScenario(points);
      var v=aggOverallCanon(currentScenario);
      window.dsCp.realScore=v==null?0:v;
      renderAll();
    })
    .catch(function(err){
      CHECK_POINT_API_LOADING=false;
      if(CHECK_POINT_API_RETRY_COUNT===0 || CHECK_POINT_API_RETRY_COUNT%20===0) cpShowErrorBadge(err.message||String(err));
      CHECK_POINT_API_READY=false;
      renderCheckPointNoApi(err.message||String(err));
      if(CHECK_POINT_API_RETRY_COUNT<CHECK_POINT_API_RETRY_MAX){
        CHECK_POINT_API_RETRY_COUNT++;
        setTimeout(loadLiveCheckPointScores,CHECK_POINT_API_RETRY_MS);
      }
    });
}

loadLiveCheckPointScores();

})();

/* ── CHECK POINT → GLOBAL CONFIDENCE wiring (real state = Mixed; W: preproc→Processing, checkpoint→Capture) ── */
(function(){
  var real = (window.dsCp && typeof window.dsCp.realScore==='number')
             ? Math.round(window.dsCp.realScore) : 58;
  if(typeof SUB_CAPTURE_CHECKPOINT!=='undefined') SUB_CAPTURE_CHECKPOINT.score = real;
  var W={drone:0.35, base:0.30, gcp:0.20, checkpoint:0.15};
  var sc={
    drone:(typeof SUB_CAPTURE_DRONE!=='undefined')?SUB_CAPTURE_DRONE.score:95,
    base:(typeof SUB_CAPTURE_BASE!=='undefined')?SUB_CAPTURE_BASE.score:87,
    gcp:(typeof SUB_CAPTURE_GCP!=='undefined')?SUB_CAPTURE_GCP.score:92,
    checkpoint:real
  };
  var capScore=Math.round(W.drone*sc.drone+W.base*sc.base+W.gcp*sc.gcp+W.checkpoint*sc.checkpoint);
  if(typeof ONTOLOGY!=='undefined' && ONTOLOGY.universes && ONTOLOGY.universes[0]){
    ONTOLOGY.universes[0].score=capScore;
    if(typeof GATES!=='undefined' && GATES[0]){GATES[0].score=capScore;if(GATES[0].universe)GATES[0].universe.score=capScore;}
    var nOJS=Math.round(ONTOLOGY.universes[0].score*ONTOLOGY.universes[0].weight +
      ONTOLOGY.universes[1].score*ONTOLOGY.universes[1].weight +
      ONTOLOGY.universes[2].score*ONTOLOGY.universes[2].weight);
    try{ OJS=nOJS; }catch(e){}  /* keep the global OJS in sync with the displayed master (this wiring runs last) */
    var ms=document.getElementById('ms-num');
    if(ms) ms.innerHTML=nOJS+'<span style="font-size:.28em;font-weight:700;color:rgba(235,242,248,.38);vertical-align:super;line-height:0;">%</span>';
    var st=document.getElementById('sentence-text');
    if(st) st.innerHTML='Pitpack 4 scored <strong>'+nOJS+'%</strong> on the Infinity Loop &mdash; up 2.3% from last survey, trending toward Professional Grade across 11 missions.';
    if(typeof buildScoreLabels==='function'){try{buildScoreLabels();}catch(e){}}
  }
})();
var buildCheckpointPage = function(){
  if(window.dsCp){
    if(window.dsCp.refreshApi) window.dsCp.refreshApi();
    window.dsCp.render();
  }
};
