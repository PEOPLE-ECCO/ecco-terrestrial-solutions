cwlVersion: v1.2
class: CommandLineTool
label: Run the disturbance index workflow with CWL-supplied inputs
doc: |
  Runs VDO_disturbance_index/disturbance_index_cwl.py inside the disturbance
  index image, built from tooling/disturbance-index/disturbance_index.Dockerfile.

  The image ENTRYPOINT is `python /app/disturbance_index_cwl.py`, so this tool
  supplies only the arguments.
requirements:
  InlineJavascriptRequirement: {}
  DockerRequirement:
    dockerPull: ghcr.io/people-ecco/hatfield-disturbance-index:latest
inputs:
  zones_polys:
    type: File
    inputBinding:
      prefix: --zones-polys
  breaks_raster:
    type: File
    inputBinding:
      prefix: --breaks-raster
  fires_points:
    type: File
    inputBinding:
      prefix: --fires-points
  built_raster:
    type: File
    inputBinding:
      prefix: --built-raster
  output:
    type: string
    default: disturbance_index.geojson
    doc: Output file name, written to the job output directory.
    inputBinding:
      prefix: --output
  mmu_area:
    type: float?
    default: 5000.0
    inputBinding:
      prefix: --mmu-area
  majority_filter_size:
    type: int?
    default: 7
    inputBinding:
      prefix: --majority-filter-size
  connectivity:
    type: int?
    default: 8
    inputBinding:
      prefix: --connectivity
  weights:
    type: string?
    default: "0.3,0.7,0.9"
    inputBinding:
      prefix: --weights
  cap_percentile:
    type: float?
    default: 99.0
    inputBinding:
      prefix: --cap-percentile
  magnitude_threshold:
    type: float?
    default: -200.0
    inputBinding:
      prefix: --magnitude-threshold
  disable_opencv:
    type: boolean?
    default: false
    doc: Disable OpenCV-backed majority filtering (enabled by default).
    inputBinding:
      prefix: --no-use-opencv
outputs:
  result:
    type: File
    outputBinding:
      glob: $(inputs.output)
