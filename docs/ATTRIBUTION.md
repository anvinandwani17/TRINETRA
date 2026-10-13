# Sources and attribution

- Sentinel-1 imagery: Copernicus Sentinel data, accessed through Copernicus Data Space Ecosystem (https://dataspace.copernicus.eu/). Acquisition times and product identifiers are recorded in provenance.json.
- Terrain: Copernicus DEM, accessed through the existing preprocessing workflow. See https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM. DEM resolution and terrain-screen limitations must be retained.
- Infrastructure and basemap: © OpenStreetMap contributors; OSM database under ODbL (https://www.openstreetmap.org/copyright). Historical extracts via HeiGIT ohsome (https://ohsome.org/copyrights). Requested snapshot, availability and response hashes are recorded in provenance.json. Map tiles are a current basemap, not the historical analysis snapshot.
- AI training dataset: Kuro Siwo, Bountos et al. (2024), via https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset. The checkpoint's actual subset and event split must be disclosed. Dataset use remains subject to its source terms.
- Reference context, when separately provided: Copernicus Emergency Management Service, EMSR927 (https://mapping.emergency.copernicus.eu/activations/EMSR927/). The supplied Bidur labels are mass movement/landslide context, not flood-water ground truth or training labels. The current export does not calculate corridor agreement.

Third-party data terms are separate from project code licensing. No project code license is selected by this attribution file. Release publishers must choose and declare the project's code license and check the original asset redistribution terms.
