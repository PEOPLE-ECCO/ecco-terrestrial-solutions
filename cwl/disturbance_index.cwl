cwlVersion: v1.2
class: CommandLineTool
label: Run the disturbance index workflow with CWL-supplied inputs
requirements:
  InlineJavascriptRequirement: {}
baseCommand: [python, disturbance_index_cwl.py]
inputs:
  zones_polys:
    type: string
    inputBinding:
      prefix: --zones-polys
  breaks_raster:
    type: string
    inputBinding:
      prefix: --breaks-raster
  fires_points:
    type: string
    inputBinding:
      prefix: --fires-points
  built_raster:
    type: string
    inputBinding:
      prefix: --built-raster
  output:
    type: string
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
  use_opencv:
    type: boolean?
    default: true
    inputBinding:
      valueFrom: $(self ? "--use-opencv" : "--no-use-opencv")
outputs:
  result:
    type: File
    outputBinding:
      glob: $(inputs.output)
