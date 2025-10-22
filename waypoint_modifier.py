import os
import copy
import datetime

import math
from lxml import etree
from zipfile import ZipFile, ZIP_DEFLATED


# ----------------------------
# Configuration / XPath helpers
# ----------------------------
WPML_NS = "http://www.dji.com/wpmz/1.0.6"
KML_NS = "http://www.opengis.net/kml/2.2"
ns = {"wpml": WPML_NS, "kml": KML_NS}


# Where Placemarks live: typical is /kml:kml/kml:Document//kml:Placemark
PLACEMARKS_XP = ".//kml:Placemark"
# Coordinates: kml:Point/kml:coordinates -> "lon,lat[,alt]"
COORDS_XP = ".//kml:Point/kml:coordinates"
# Optional wpml fields commonly found under Placemark (sometimes in ExtendedData):
INDEX_XP = ".//wpml:index"
EXEC_HEIGHT_XP = ".//wpml:executeHeight"
SPEED_XP = ".//wpml:waypointSpeed"
# Use whichever your file has — here we try both; we will write back to whichever we found first:
HEADING_ANGLE_XP = ".//wpml:waypointHeadingAngle"
HEADING_PARAM_ANGLE_XP = ".//wpml:waypointHeadingParam/wpml:waypointHeadingAngle"

# Action groups live under the main Document node in most DJI files
ACTION_GROUPS_PARENT_XP = ".//kml:Placemark"
ACTION_GROUP_XP = ".//wpml:actionGroup"

# KML id for finding the mission template type
TEMPLATE_XP = ".//wpml:templateType"
# Required template id that indicates this is a mapping mission
MISSION_ID = 'mapping2d'

def _text(el):
    return el.text if el is not None else None

# ----------------------------
# Geometry helpers
# ----------------------------
EARTH_R = 6371000.0  # meters

    
def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance (meters)."""
    to_rad = math.radians
    dlat = to_rad(lat2 - lat1)
    dlon = to_rad(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return EARTH_R * c

def lerp(a, b, t):
    return a + (b - a) * t

def lerp_angle_deg(a, b, t):
    """Shortest-path interpolation of degrees a->b."""
    # Normalize to [-180,180)
    def norm(x): 
        x = (x + 180.0) % 360.0 - 180.0
        return x
    a_n, b_n = norm(a), norm(b)
    delta = norm(b_n - a_n)
    return norm(a_n + delta * t)

# ----------------------------
# Parsing Placemarks into a list
# ----------------------------
def read_placemarks(root):
    """Return list of dicts: index, lat, lon, executeHeight, speed, heading, el (element), and heading_key."""
    placemarks = []
    for idx, pm in enumerate(root.xpath(PLACEMARKS_XP, namespaces=ns)):
        # coords
        coords_el = pm.xpath(COORDS_XP, namespaces=ns)
        if not coords_el:
            continue
        lon_lat_alt = coords_el[0].text.strip().split(",")
        lon = float(lon_lat_alt[0])
        lat = float(lon_lat_alt[1])

        # fields (None if missing)
        exe_el = pm.xpath(EXEC_HEIGHT_XP, namespaces=ns)
        spd_el = pm.xpath(SPEED_XP, namespaces=ns)
        hdg_el = pm.xpath(HEADING_ANGLE_XP, namespaces=ns)
        if not hdg_el:
            hdg_el = pm.xpath(HEADING_PARAM_ANGLE_XP, namespaces=ns)

        execute_height = float(_text(exe_el[0])) if exe_el and _text(exe_el[0]) else None
        speed = float(_text(spd_el[0])) if spd_el and _text(spd_el[0]) else None
        heading = float(_text(hdg_el[0])) if hdg_el and _text(hdg_el[0]) else None

        heading_key = None
        if pm.xpath(HEADING_ANGLE_XP, namespaces=ns):
            heading_key = "angle_direct"
        elif pm.xpath(HEADING_PARAM_ANGLE_XP, namespaces=ns):
            heading_key = "angle_param"

        placemarks.append({
            "index": idx,
            "lat": lat,
            "lon": lon,
            "executeHeight": execute_height,
            "speed": speed,
            "heading": heading,
            "el": pm,
            "heading_key": heading_key,
        })
    return placemarks

# ----------------------------
# Resampling by time interval s
# ----------------------------
def segment_duration_s(p0, p1):
    """Estimate segment duration using average of endpoint speeds."""
    v0 = p0["speed"] if p0["speed"] and p0["speed"] > 0 else None
    v1 = p1["speed"] if p1["speed"] and p1["speed"] > 0 else None
    dist = haversine_m(p0["lat"], p0["lon"], p1["lat"], p1["lon"])
    if v0 and v1:
        v_avg = 0.5*(v0 + v1)
    elif v0:
        v_avg = v0
    elif v1:
        v_avg = v1
    else:
        v_avg = 5.0  # sensible default if speeds are missing
    return dist / max(v_avg, 0.01), dist

def resample_waypoints_time_uniform(pls, s_seconds):
    """
    Build a new list of samples separated by s_seconds.
    Interpolate lat/lon, executeHeight, speed, heading.
    """
    if len(pls) < 2:
        return pls[:]  # nothing to resample

    # Build cumulative time per original vertex
    times = [0.0]
    seg_meta = []
    for i in range(len(pls)-1):
        dur, dist = segment_duration_s(pls[i], pls[i+1])
        seg_meta.append({"duration": dur, "distance": dist})
        times.append(times[-1] + dur)
    total_T = times[-1]

    # sampling times
    t_samples = []
    t = 0.0
    while t < total_T:
        t_samples.append(t)
        t += s_seconds
    if not t_samples or t_samples[-1] < total_T:
        t_samples.append(total_T)  # ensure last

    # interpolate helper: map global t to segment i and local tau
    def locate_segment(tg):
        if tg <= 0: return 0, 0.0
        if tg >= total_T: return len(pls)-2, 1.0
        # linear scan is fine; list is small; could binary search if needed
        for i in range(len(seg_meta)):
            t0 = times[i]
            t1 = times[i+1]
            if tg <= t1:
                tau = (tg - t0) / max((t1 - t0), 1e-9)
                return i, tau
        return len(pls)-2, 1.0

    new_pls = []
    for ti, tg in enumerate(t_samples):
        i, tau = locate_segment(tg)
        a = pls[i]
        b = pls[i+1]
        lat = lerp(a["lat"], b["lat"], tau)
        lon = lerp(a["lon"], b["lon"], tau)

        # interpolate optional fields if both exist, else carry forward nearest
        def interp_field(key, angle=False):
            av = a.get(key)
            bv = b.get(key)
            if av is not None and bv is not None:
                if angle:
                    return lerp_angle_deg(av, bv, tau)
                else:
                    return lerp(av, bv, tau)
            return av if tau < 0.5 else bv

        executeHeight = interp_field("executeHeight", angle=False)
        speed = interp_field("speed", angle=False)
        heading = interp_field("heading", angle=True)

        new_pls.append({
            "index": ti,  # will be re-assigned later anyway
            "lat": lat,
            "lon": lon,
            "executeHeight": executeHeight,
            "speed": speed,
            "heading": heading,
            "heading_key": a.get("heading_key") or b.get("heading_key") or "angle_direct",
        })

    return new_pls

# ----------------------------
# Rebuild Placemark elements
# ----------------------------
def set_or_create_first(elem, xp, tag, text, nsmap):
    el = elem.xpath(xp, namespaces=nsmap)
    if el:
        el[0].text = str(text)
        return el[0]
    # Create the structure where it usually lives; prefer direct child:
    new_el = etree.SubElement(elem, etree.QName(WPML_NS, tag.split(":")[-1]))
    new_el.text = str(text)
    return new_el

def rebuild_placemarks(root, new_pls):
    """
    Replace existing Placemark list with new list (indices 0..N-1),
    updating coordinates + wpml fields we handle.
    """
    doc = root.xpath(".//kml:Document", namespaces=ns)
    if not doc:
        raise RuntimeError("Cannot find kml:Document")
    doc = doc[0]

    # Collect and remove existing Placemarks (preserve other nodes)
    old_pms = root.xpath(PLACEMARKS_XP, namespaces=ns)
    if not old_pms:
        raise RuntimeError("No Placemark elements found")
    parent = old_pms[0].getparent()
    for pm in old_pms:
        parent.remove(pm)

    # Template from second old placemark to preserve structure
    pm_template = old_pms[1]

    # Build new placemarks
    for i, wp in enumerate(new_pls):
        # if i==0:
        #     pm = copy.deepcopy(old_pms[0])
        # elif i==len(new_pls)-1:
        #     pm = copy.deepcopy(old_pms[-1])
        # else:
        #     pm = copy.deepcopy(pm_template)
        pm = copy.deepcopy(pm_template)
        # coordinates
        coords_el = pm.xpath(COORDS_XP, namespaces=ns)
        if coords_el:
            coords_el[0].text = f"{wp['lon']:.9f},{wp['lat']:.9f}"
        else:
            # create minimal Point/coordinates
            point_el = etree.SubElement(pm, etree.QName(KML_NS, "Point"))
            coords = etree.SubElement(point_el, etree.QName(KML_NS, "coordinates"))
            coords.text = f"{wp['lon']:.9f},{wp['lat']:.9f}"

        # wpml fields
        if wp["index"] is not None:
            set_or_create_first(pm, INDEX_XP, "wpml:index", f"{wp['index']}", ns)
        if wp["executeHeight"] is not None:
            set_or_create_first(pm, EXEC_HEIGHT_XP, "wpml:executeHeight", f"{wp['executeHeight']:.6f}", ns)
        if wp["speed"] is not None:
            set_or_create_first(pm, SPEED_XP, "wpml:waypointSpeed", f"{wp['speed']:.6f}", ns)
        if wp["heading"] is not None:
            if wp["heading_key"] == "angle_param":
                set_or_create_first(pm, HEADING_PARAM_ANGLE_XP, "wpml:waypointHeadingAngle", f"{wp['heading']:.6f}", ns)
            else:
                set_or_create_first(pm, HEADING_ANGLE_XP, "wpml:waypointHeadingAngle", f"{wp['heading']:.6f}", ns)

        parent.append(pm)
# Need to set actionGroupStartIndex and actionGroupStopIndex and <wpml:actionGroupId> to last for stopTimeLapse
# ----------------------------
# ActionGroup utilities
# ----------------------------
def next_ids(root):
    """Find next available actionGroupId and actionId values (max+1)."""
    max_gid = -1
    max_aid = -1
    for ag in root.xpath(ACTION_GROUP_XP, namespaces=ns):
        gid_el = ag.find(f"{{{WPML_NS}}}actionGroupId")
        if gid_el is not None:
            try: max_gid = max(max_gid, int(gid_el.text))
            except: pass
        for act in ag.findall(f"{{{WPML_NS}}}action"):
            aid_el = act.find(f"{{{WPML_NS}}}actionId")
            if aid_el is not None:
                try: max_aid = max(max_aid, int(aid_el.text))
                except: pass
    return max_gid + 1, max_aid + 1

def ensure_action_groups_parent(root, idx=0):
    parents = root.xpath(ACTION_GROUPS_PARENT_XP, namespaces=ns)
    if not parents:
        raise RuntimeError("Cannot find kml:Document to place actionGroups")
    return parents[idx]

def make_action_group(group_id, start_idx, end_idx, trigger_type, actions, total_count=1e9):
    if trigger_type == "betweenAdjacentPoints":
        # DJI cannot handle betweenAdjacentPoints on endpoints
        start_idx = max(start_idx, 1)
        end_idx = min(end_idx, total_count - 1)
        if start_idx >= end_idx:
            return None
    ag = etree.Element(etree.QName(WPML_NS, "actionGroup"))
    gid = etree.SubElement(ag, etree.QName(WPML_NS, "actionGroupId"))
    gid.text = str(group_id)
    sidx = etree.SubElement(ag, etree.QName(WPML_NS, "actionGroupStartIndex"))
    sidx.text = str(start_idx)
    eidx = etree.SubElement(ag, etree.QName(WPML_NS, "actionGroupEndIndex"))
    eidx.text = str(end_idx)
    mode = etree.SubElement(ag, etree.QName(WPML_NS, "actionGroupMode"))
    mode.text = "sequence"
    trig = etree.SubElement(ag, etree.QName(WPML_NS, "actionTrigger"))
    ttype = etree.SubElement(trig, etree.QName(WPML_NS, "actionTriggerType"))
    ttype.text = trigger_type

    for act in actions:
        ag.append(act)
    return ag

def make_action(aid, func_name, func_params_dict):
    action = etree.Element(etree.QName(WPML_NS, "action"))
    aid_el = etree.SubElement(action, etree.QName(WPML_NS, "actionId"))
    aid_el.text = str(aid)
    fn = etree.SubElement(action, etree.QName(WPML_NS, "actionActuatorFunc"))
    fn.text = func_name
    params = etree.SubElement(action, etree.QName(WPML_NS, "actionActuatorFuncParam"))
    # minimal params supported here
    for key, val in func_params_dict.items():
        child = etree.SubElement(params, etree.QName(WPML_NS, key))
        child.text = str(val)
    return action

# ----------------------------
# Requirement handlers
# ----------------------------
def remove_gimbalRotate_on_first(root):
    """Remove gimbalRotate actions that apply to Placemark 0 for triggers: multipleTiming, betweenAdjacentPoints."""
    groups = root.xpath(ACTION_GROUP_XP, namespaces=ns)
    to_remove_groups = []
    for ag in groups:
        trig_el = ag.find(f"{{{WPML_NS}}}actionTrigger")
        trig_type = trig_el.find(f"{{{WPML_NS}}}actionTriggerType").text if trig_el is not None else ""
        if trig_type not in ("multipleTiming", "betweenAdjacentPoints"):
            continue
        s_idx = ag.find(f"{{{WPML_NS}}}actionGroupStartIndex")
        e_idx = ag.find(f"{{{WPML_NS}}}actionGroupEndIndex")
        try:
            s_i = int(s_idx.text) if s_idx is not None else 0
            e_i = int(e_idx.text) if e_idx is not None else 0
        except:
            continue
        # does it cover Placemark 0?
        if not (s_i <= 0 <= e_i):
            continue

        # remove actions named gimbalRotate
        removed_any = False
        for act in list(ag.findall(f"{{{WPML_NS}}}action")):
            func = act.find(f"{{{WPML_NS}}}actionActuatorFunc")
            if func is not None and func.text == "gimbalRotate":
                ag.remove(act)
                removed_any = True
        # if group becomes empty (no actions), remove whole group
        if removed_any and not ag.findall(f"{{{WPML_NS}}}action"):
            to_remove_groups.append(ag)

    for ag in to_remove_groups:
        ag.getparent().remove(ag)

def add_start_stop_record(root, first_index, last_index, payload_position_index=0, use_global_lens=True, lens_index_text="visable,ir"):
    parent = ensure_action_groups_parent(root)
    next_gid, _ = next_ids(root)
    next_aid = 0
    # startRecord @ first_index
    start_params = {
        "payloadPositionIndex": payload_position_index,
    }
    # “useGlobalPayloadLensIndex” and “payloadLensIndex” are included if desired:
    if use_global_lens:
        start_params["useGlobalPayloadLensIndex"] = 1
        start_params["payloadLensIndex"] = lens_index_text

    start_action = make_action(next_aid, "startRecord", start_params)
    ag_start = make_action_group(next_gid, first_index, first_index, "reachPoint", [start_action])
    parent.append(ag_start)
    next_gid += 1
    next_aid += 1

    # stopRecord @ last_index
    parent = ensure_action_groups_parent(root, -1)
    stop_action = make_action(next_aid, "stopRecord", {"payloadPositionIndex": payload_position_index})
    ag_stop = make_action_group(next_gid, last_index, last_index, "reachPoint", [stop_action])
    parent.append(ag_stop)

def add_start_timelapse(root, first_index, last_index, payload_position_index=0, use_global_lens=True, lens_index_text="visable,ir"):
    parent = ensure_action_groups_parent(root)
    next_gid, _ = next_ids(root)
    next_aid = 0
    # startRecord @ first_index
    start_params = {
        "payloadPositionIndex": payload_position_index,
        "minShootInterval": 0.50497353076935
    }
    # “useGlobalPayloadLensIndex” and “payloadLensIndex” are included if desired:
    if use_global_lens:
        start_params["useGlobalPayloadLensIndex"] = 0
        start_params["payloadLensIndex"] = lens_index_text

    start_action = make_action(next_aid, "startTimeLapse", start_params)
    ag_start = make_action_group(next_gid, first_index, last_index, "betweenAdjacentPoints", [start_action], total_count=last_index+1)
    parent.append(ag_start)


def add_stop_timelapse(root, last_index, payload_position_index=0, use_global_lens=True, lens_index_text="visable,ir"):
    parent = ensure_action_groups_parent(root)
    next_gid, _ = next_ids(root)
    next_aid = 0
    # startRecord @ first_index
    start_params = {
        "payloadPositionIndex": payload_position_index,
    }
    # “useGlobalPayloadLensIndex” and “payloadLensIndex” are included if desired:
    if use_global_lens:
        start_params["useGlobalPayloadLensIndex"] = 0
        start_params["payloadLensIndex"] = lens_index_text

    # stopRecord @ last_index
    parent = ensure_action_groups_parent(root, -1)
    stop_action = make_action(next_aid, "stopTimeLapse", start_params)
    ag_stop = make_action_group(next_gid, last_index, last_index, "reachPoint", [stop_action])
    parent.append(ag_stop)
    
def add_gimbal_evenly_rotate_blocks(root, n, total_count, payload_position_index=0):
    """
    Create blocks of size n, alternating -90, +0, spanning indices:
      [0..n], [n..2n], [2n..3n], ...
    Uses betweenAdjacentPoints trigger.
    """
    if n <= 0 or total_count <= 1:
        return
    parent = ensure_action_groups_parent(root)
    next_gid, _ = next_ids(root)
    next_aid = 0
    #print(f"next_gid = {next_gid}; next_aid = {next_aid}")
    
    # Determine number of blocks
    # We want contiguous ranges; end index is inclusive but must be <= total_count-1
    block_idx = 0
    start = 0
    while start < (total_count - 1):
        end = min(start + n, total_count - 1)
        angle = -90 if (block_idx % 2 == 0) else 0
        action = make_action(next_aid, "gimbalEvenlyRotate", {
            "gimbalPitchRotateAngle": angle,
            "payloadPositionIndex": payload_position_index
        })
        if start == 0 or end == total_count - 1:
            trigger_type = "reachedWaypoint"
        else:
            trigger_type = "betweenAdjacentPoints"
        ag = make_action_group(next_gid, start, end, trigger_type, [action], total_count)
        parent.append(ag)

        next_gid += 1
        #next_aid += 1
        block_idx += 1
        start = end

# ----------------------------
# Orchestrator
# ----------------------------
def apply_all_mods(in_root_wpml, s_seconds: float, n_block: int):
    """
    - Read original Placemarks
    - Remove gimbalRotate for first Placemark in specified triggers
    - Resample Placemarks to s-second spacing
    - Rebuild Placemark list (update indices)
    - Add startRecord@0 and stopRecord@last
    - Add alternating gimbalEvenlyRotate blocks of size n_block
    """
    # 1) original placemarks
    orig_pls = read_placemarks(in_root_wpml)
    if len(orig_pls) < 1:
        raise RuntimeError("No Placemarks found.")

    # 2) remove unwanted gimbalRotate actions (first placemark = index 0)
    remove_gimbalRotate_on_first(in_root_wpml)

    # 3) resample
    resampled = resample_waypoints_time_uniform(orig_pls, s_seconds)
    last_idx = len(resampled) - 1

    # 4) rebuild Placemark list
    rebuild_placemarks(in_root_wpml, resampled)
    add_start_timelapse(in_root_wpml, first_index=0, last_index=last_idx)

    # 5) add alternating gimbalEvenlyRotate blocks (betweenAdjacentPoints)
    add_gimbal_evenly_rotate_blocks(in_root_wpml, n=n_block, total_count=len(resampled))

    # 6) add start/stop record with new last index
    add_start_stop_record(in_root_wpml, first_index=0, last_index=last_idx)

    add_stop_timelapse(in_root_wpml, last_index=last_idx)
    return in_root_wpml, resampled


def get_mission_type(kml_data):
    root =  etree.fromstring(kml_data)
    templates = root.xpath(TEMPLATE_XP, namespaces=ns)
    return templates[0].text


def read_kmz(kmz_in_fname):
    # Extract the KML and WPML files from the KMZ
    assert os.path.isfile(kmz_in_fname), f'FATAL ERROR: Cannot open {kmz_in_fname}. Does it exist?'

    with ZipFile(kmz_in_fname, 'r') as z:
        try:
            wpml_filename = [name for name in z.namelist() if name.endswith('.wpml')][0]
            wpml_data = z.read(wpml_filename)
        except:
            print(f'FATAL ERROR: Failed to read .wmpl file in {kmz_in_fname}')
            return None, None

        try:
            kml_filename = [name for name in z.namelist() if name.endswith('.kml')][0]
            kml_data = z.read(kml_filename)
        except:
            print(f'Failed to read .kml file in {kmz_in_fname}')
            print(f'Unable to verify kml/wpml mission type - proceed with caution')
            return wpml_data, None

    mission_type = get_mission_type(kml_data)
    assert mission_type==MISSION_ID, f"FATAL ERROR: Wrong mission type - expected '{MISSION_ID}' and received '{mission_type}'"
    return wpml_data, kml_data

    
# ----------------------------
# Pretty-print & write-back helpers
# ----------------------------
def serialize_wpml(root):
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)

# ----------------------------
# Convert area route
# ----------------------------
def convert_area_route(kmz_in_fname, s_seconds, n_block, debug_wpml_output=False):
    # Read wpml data from kmz file
    wpml_data, kml_data = read_kmz(kmz_in_fname)
    
    # Parse from string
    in_root_wpml = etree.fromstring(wpml_data)
    
    # Convert area map mission to new video collection mission
    out_root, resampled = apply_all_mods(
        in_root_wpml=in_root_wpml, 
        s_seconds=s_seconds, 
        n_block=n_block
    )
    new_wpml_bytes = serialize_wpml(out_root)

    # Write back into a copy of the KMZ
    dt = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    if debug_wpml_output:
        wpml_out_fname = kmz_in_fname.replace(".kmz", f"-MODIFIED-{dt}.xml")
        with open(wpml_out_fname, "wb") as f:
            f.write(new_wpml_bytes)
    out_kmz_path = kmz_in_fname.replace(".kmz", f"-MODIFIED-{dt}.kmz")
    with ZipFile(kmz_in_fname, 'r') as zin, ZipFile(out_kmz_path, 'w', compression=ZIP_DEFLATED) as zout:
        wpml_name = [n for n in zin.namelist() if n.endswith(".wpml")][0]
        # copy everything except the .wpml
        for name in zin.namelist():
            if name != wpml_name:
                zout.writestr(name, zin.read(name))
        # write modified wpml
        zout.writestr(wpml_name, new_wpml_bytes)
    print("Wrote:", out_kmz_path)

    return 1


def modify_waypoints(input_path):
    s_seconds = 2.0   # desired time spacing between Placemarks
    n_block = 5       # evenly rotate over every 5-placemark block, alternating

    return convert_area_route(input_path, s_seconds, n_block, debug_wpml_output=False)


