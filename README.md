# When the Seafloor Becomes Mountains

**30DayMapChallenge · Day 15 · Inside Out**  
Ananda Dewi (Geonoryx)

The default study area covers southern Java and the Java Trench, Indonesia. The map compares original signed seafloor elevation with an artistic sign inversion. A cell at −6,000 m becomes +6,000 m in the inverted view. Both views use the same cell centres and the same 3D vertical axis limits. The exaggeration slider changes only the rendered proportions.

## 1. Process the data in Google Colab

1. Extract the ZIP. Open [Google Colab](https://colab.research.google.com/), choose **File → Upload notebook**, then upload **Day15_InsideOut_Colab.ipynb**.
2. Run the cells from top to bottom. Approve the Drive mount with the Google account that should own the outputs.
3. Leave the default study area for the Java Trench. To change it, edit `BOUNDS = (west, south, east, north)` and `REGION_NAME` in the settings cell.
4. Wait for both **Published** messages. Each completed run gets its own UTC timestamp and ID.

The notebook is self-contained. The separate `colab_pipeline.py` contains the same processing functions for editing in VS Code. Colab reads byte ranges from the GEBCO cloud-optimized GeoTIFF and downloads the requested native-resolution window. It retries temporary source failures and reuses the validated local subset when the same Colab runtime remains available.

### Drive outputs

| Location inside My Drive | Contents |
| --- | --- |
| `InsideOut_GEBCO/terrain-<run-id>.json` | Browser grid, original seafloor elevations, WGS84 coordinates and metadata. Python fetches this file and JavaScript calculates the matching flipped heights. |
| `InsideOut_GEBCO/gis/<run-id>/gebco_2026_subset.tif` | Native 15-arc-second crop, including both land and ocean. |
| `InsideOut_GEBCO/gis/<run-id>/original_bathymetry_m.tif` | Native underwater cells in signed metres. |
| `InsideOut_GEBCO/gis/<run-id>/inverted_relief_m.tif` | Native artistic heights in metres. |
| `InsideOut_GEBCO/gis/<run-id>/metadata.json` | Bounds, dimensions, processing notes and source attribution. |

The source crop includes each native cell that intersects the requested bounds. Both derived GeoTIFFs preserve the native affine transform and use `-32768` as NoData. The browser samples up to 260 cells per side by default. Its deepest visible cell can differ from the deepest native cell. The web release is written after the four GIS files have been copied and verified.

## 2. Connect the local WebGIS to Drive

The WebGIS reads a private Google Drive folder through the Google Drive API. Google Drive for desktop is optional and can be skipped. You need Python 3.10 or later and a browser on the same computer as VS Code.

### Set up the Google OAuth client once

1. In [Google Cloud Console](https://console.cloud.google.com/), select or create a project and [enable Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com).
2. Under **Google Auth Platform**, configure **Branding** and **Audience**. For a personal Google account with an external app in testing, add the Google account you used in Colab under **Audience → Test users**.
3. Under **Data Access**, add the scope `https://www.googleapis.com/auth/drive.readonly`. The scope allows the Python client to find and download the file. The app only queries the project folder and downloads its latest terrain release.
4. Open **Clients → Create client**, choose **Desktop app**, and download its JSON. Rename it to `credentials.json` and place it inside **web**, next to `app.py`.
5. On the first run, select the same Google account used in Colab. Python saves the authorization to `web/token.json` for subsequent runs.

[Google Drive Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python)

### Run from the VS Code terminal on Windows

Open the extracted **InsideOut_GEBCO** folder in VS Code, then run:

```powershell
cd web
py -m pip install -r requirements.txt
py app.py
```

If Windows uses `python` instead of `py`, use `python` in both commands. After OAuth returns to the app, open [http://127.0.0.1:8000](http://127.0.0.1:8000). Python finds `InsideOut_GEBCO` in the signed-in account’s My Drive and downloads the newest complete web release. Restart `app.py` after publishing another release in Colab. Plotly is bundled, so the browser does not need a CDN connection.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| The source returns a timeout or range error | Rerun the last notebook cell. If the host remains unavailable, set `LOCAL_GEOTIFF` to a native GEBCO_2026 GeoTIFF covering the selected bounds, then rerun the last cell. |
| My Drive cannot be mounted | In Colab, rerun the Drive mount cell and complete its permission prompt. |
| Drive API says the project folder or release is missing | Check My Drive for `InsideOut_GEBCO/terrain-*.json`. Confirm you authorized the same account in Colab and VS Code. |
| Drive reports duplicate project folders | Use `py app.py --folder-id YOUR_FOLDER_ID` with the portion after `/folders/` in the intended folder URL. |
| Drive returns `access_denied` or `accessNotConfigured` | Add your OAuth test user if the external app is in testing, then check that Drive API is enabled in the project that owns the desktop OAuth client. Workspace administrators can restrict access to scopes. |
| OAuth uses the wrong account or the client was replaced | Stop the server and delete the local `web/token.json`. Start again and choose the intended account. External apps in testing can require periodic reauthorization. |
| VS Code reports a missing Google module | Run the install command with the same Python launcher you use for `app.py`. |
| Port 8000 is busy | Run `py app.py --port 8001` and open [http://127.0.0.1:8001](http://127.0.0.1:8001). |
| WebGL is unavailable | The map opens the same cells in 2D. The Original depth and Inverted height buttons remain available. |
| Drive is temporarily unavailable after a successful sync | Run `py app.py --allow-cache`. The page labels the previous release as local cache. |
| You have a local copy of a terrain release | Run `py app.py --local-file "PATH_TO_TERRAIN_JSON"`. The page labels the data as a local file. |

Keep your OAuth files private. The server listens on localhost, and browser routes only expose the WebGIS assets and its terrain grid.

## Data credit

GEBCO Bathymetric Compilation Group (2026). *The GEBCO_2026 Grid*. NERC EDS British Oceanographic Data Centre NOC. [Dataset DOI](https://doi.org/10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa) · [Dataset documentation](https://www.gebco.net/data-products-gridded-bathymetry-data/gebco2026-grid) · [COG distribution](https://source.coop/ausantarctic/gebco). For artistic visualization. GEBCO prohibits use for navigation or safety at sea under its [terms of use](https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use).

Bundled `plotly.min.js` is Plotly.js 3.3.1. See `web/PLOTLY-LICENSE.txt`.
