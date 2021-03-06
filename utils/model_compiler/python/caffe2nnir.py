import os
import caffe_pb2
from nnir import *
import sys
import argparse
import struct
import math

# mapping from caffe layer types to nnir operators.
# pooling is mapped to either avg_pool or max_pool
# scale is fused to batch_norm if its previous layer is batch_norm or fused to mul or muladd in nnir.
caffe2ir_op_type = {
    'Convolution': 'conv',
    'Deconvolution': 'conv_transpose',
    'BatchNorm' : 'batch_norm',
    'InnerProduct' : 'gemm',
    'ReLU' : 'relu',
    'LRN' : 'lrn',
    'Eltwise' : 'sum',
    'Concat' : 'concat',
    'Softmax' : 'softmax',
    'SoftmaxWithLoss' : 'softmax'
}

# convert caffename to ir names.
def caffe_name_to_ir_name(name):
    return '_'.join(('_'.join(name.split('/')).split('-')))

# convert caffe blobs to ir tensor.
def caffe_blob_to_ir_tensor(blob_name, blob_data_type, blob_shape):
    tensor = IrTensor()
    tensor.setName(caffe_name_to_ir_name(blob_name))
    tensor.setInfo(blob_data_type, [int(x) for x in blob_shape])
    return tensor

# convert caffe bin formats to ir bin formats.
def convert_caffe_bin_to_ir_bin(floatlist):
    buf = struct.pack('%sf' % len(floatlist), *floatlist)
    return buf

# map caffe attr to ir attr.
def caffe_attr_to_ir_attr(attribute_map):
    attr = IrAttr()
    attr_names = attribute_map.keys()
    for i in range(len(attr_names)):
        attributeInfo = attribute_map[attr_names[i]]
        if type(attributeInfo) is float:
            attr.set(attr_names[i], float(attributeInfo))
        elif type(attributeInfo) is int:
            attr.set(attr_names[i], int(attributeInfo))
        elif type(attributeInfo) is str:
            attr.set(attr_names[i], str(attributeInfo))
        elif type(attributeInfo) == type([]):
            if (type(attributeInfo[0]) is int):
                attr.set(attr_names[i], [int(v) for v in (attributeInfo)])
            elif (type(attributeInfo[0]) is float):
                attr.set(attr_names[i], [float(v) for v in (attributeInfo)])
            else:
                print ("ERROR: unsupported list attribute")
                sys.exit(1)
        else:
            print ("ERROR: Unsupported type of caffe attribute %s" % attr_names[i])
            sys.exit(1)
    return attr

# map caffe node to ir node.
def caffe_node_to_ir_node(layer_type, layer_info_map):
    node = IrNode()
    input_map = layer_info_map["inputs"]
    output_map = layer_info_map["outputs"]
    weight_map = {}
    scale_map_w = {}
    scale_map_b = {}
    bias_map_b = {}
    if ("scale_weights" in layer_info_map):
        scale_map_w = layer_info_map["scale_weights"]
    if ("scale_bias" in layer_info_map):
        scale_map_b = layer_info_map["scale_bias"]
    if "weights" in layer_info_map:
        weight_map = layer_info_map["weights"]
    bias_map = {}
    if "biases" in layer_info_map:
        bias_map = layer_info_map["biases"]
    attribute_map = layer_info_map["attributes"]

    inputs = []
    for i in range(len(input_map.keys())):
        inputs.append(input_map.keys()[i])
    for i in range(len(scale_map_w)):
        inputs.append(scale_map_w.keys()[i])
    for i in range(len(scale_map_b)):
        inputs.append(scale_map_b.keys()[i])
    for i in range(len(weight_map.keys())):
        inputs.append(weight_map.keys()[i])
    for i in range(len(bias_map.keys())):
        inputs.append(bias_map.keys()[i])

    outputs = []
    for i in range(len(output_map.keys())):
        outputs.append(output_map.keys()[i])

    node.set(layer_type, [caffe_name_to_ir_name(name) for name in inputs],\
                         [caffe_name_to_ir_name(name) for name in outputs],\
                         caffe_attr_to_ir_attr(attribute_map))
    return node

# extract binary data from caffe layers if present.
def extractBinary(layer_parameter, graph, verbose):
    layer_name = caffe_name_to_ir_name(layer_parameter.name)
    if (verbose):
        print ("Extracting binaries from : "  + layer_name)

    ## dump weights and biases if present.
    blob_size = len(layer_parameter.blobs)
    if blob_size > 0:
        weight_blob_proto = layer_parameter.blobs[0]
        weight_len = len(weight_blob_proto.data)
        weight_blob_name = caffe_name_to_ir_name(layer_name + '_w')
        if (verbose):
            print (weight_blob_name)
        buf = convert_caffe_bin_to_ir_bin(weight_blob_proto.data)
        graph.addBinary(weight_blob_name, buf)

    if blob_size > 1:
        bias_blob_proto = layer_parameter.blobs[1]
        bias_len = len(bias_blob_proto.data)
        bias_blob_name = caffe_name_to_ir_name(layer_name + '_b')
        if (verbose):
            print (bias_blob_name)
        blob_data_type = "F032"
        buf = convert_caffe_bin_to_ir_bin(bias_blob_proto.data)
        graph.addBinary(bias_blob_name, buf)

# extracting input from caffe network and converting into ir input.
def extractInput(net_parameter, graph, input_dims):
    inputList = {}
    layers = net_parameter.layer
    first_layer_param = layers[0]
    first_layer_param_type = first_layer_param.type
    input_name = ""
    if len(net_parameter.input) != 0:
        input_name = caffe_name_to_ir_name(net_parameter.input[0])
    elif (first_layer_param_type == "Data" or first_layer_param_type == "Input" or first_layer_param_type == "ImageData"):
        top_list = first_layer_param.top
        if (len(top_list) == 0):
            input_name = caffe_name_to_ir_name(first_layer_param.name)
        else:
            input_name = caffe_name_to_ir_name(top_list[0])
    else:
        bottom_list = first_layer_param.bottom
        if (len(bottom_list) == 0):
            top_list = first_layer_param.top
            input_name = caffe_name_to_ir_name(top_list[0])
        else:
            input_name = caffe_name_to_ir_name(bottom_list[0])

    inputList[str(input_name)] = input_dims
    graph.addInput(caffe_blob_to_ir_tensor(input_name, "F032", input_dims))
    return inputList

# extraction of output from caffe network to ir output.
def extractOutput(graph, inputOutputLayers, verbose):
    outputList = {}
    last_layer_index = len(inputOutputLayers) - 1
    last_layer_info = inputOutputLayers[last_layer_index]
    output_map = last_layer_info["outputs"]
    output_name = output_map.keys()[0]
    if (verbose):
        print ("output name is : " + output_name)
    output_dims = output_map[output_name]
    graph.addOutput(caffe_blob_to_ir_tensor(output_name, "F032", output_dims))
    outputList[output_name] = output_dims
    return outputList


# extract layer attribute information from caffe layers.
def extractCaffeAttrInfo(layer_param):
    layer_type = layer_param.type
    attribute_map = {}
    if (layer_type == "Convolution" or layer_type == "Deconvolution"):
        conv = layer_param.convolution_param
        pad_h = conv.pad_h if (conv.HasField('pad_h')) else (int(conv.pad[0]) if (len(conv.pad) > 0) else 0)
        pad_w = conv.pad_w if (conv.HasField('pad_w')) else (int(conv.pad[1]) if (len(conv.pad) > 1) else pad_h)
        stride_h = conv.stride_h if (conv.HasField('stride_h')) else (int(conv.stride[0]) if (len(conv.stride) > 0) else 1)
        stride_w = conv.stride_w if (conv.HasField('stride_w')) else (int(conv.stride[1]) if (len(conv.stride) > 1) else stride_h)
        kernel_h = conv.kernel_h if (conv.HasField('kernel_h')) else (int(conv.kernel_size[0]) if (len(conv.kernel_size) > 0) else 0)
        kernel_w = conv.kernel_w if (conv.HasField('kernel_w')) else (int(conv.kernel_size[1]) if (len(conv.kernel_size) > 1) else kernel_h)
        num_out = conv.num_output
        dilation_h = conv.dilation[0] if (len(conv.dilation) > 0) else 1
        dilation_w = conv.dilation[1] if (len(conv.dilation) > 1) else dilation_h
        bias_term = conv.bias_term
        groups = conv.group if (conv.HasField('group')) else 1

        attribute_map["strides"] = [stride_w, stride_h]
        attribute_map["kernel_shape"] = [kernel_w, kernel_h]
        attribute_map["group"] = groups
        attribute_map["pads"] = [pad_w, pad_h, pad_w, pad_h]
        attribute_map["dilations"] = [dilation_w, dilation_h]

    elif (layer_type == "Pooling"):
        pooling = layer_param.pooling_param
        pad_h = int(pooling.pad_h) if (pooling.HasField('pad_h')) else int(pooling.pad)
        pad_w = int(pooling.pad_w) if (pooling.HasField('pad_w')) else int(pooling.pad)
        stride_h = int(pooling.stride_h) if (pooling.HasField('stride_h')) else int(pooling.stride)
        stride_w = int(pooling.stride_w) if (pooling.HasField('stride_w')) else int(pooling.stride)
        kernel_h = int(pooling.kernel_h) if (pooling.HasField('kernel_h')) else int(pooling.kernel_size)
        kernel_w = int(pooling.kernel_w) if (pooling.HasField('kernel_w')) else int(pooling.kernel_size)

        attribute_map["strides"] = [stride_w, stride_h]
        attribute_map["kernel_shape"] = [kernel_w, kernel_h]
        attribute_map["pads"] = [pad_w, pad_h, pad_w, pad_h]
        attribute_map["dim_round_mode"] = "ceil"
        #attribute_map["dilations"] = [1,1]

    elif (layer_type == "LRN"):
        lrn = layer_param.lrn_param
        local_size = int(lrn.local_size)
        alpha = float(lrn.alpha)
        beta = float(lrn.beta)
        k = float(lrn.k)

        attribute_map["alpha"] = alpha
        attribute_map["beta"] = beta
        attribute_map["size"] = local_size
        attribute_map["bias"] = k

    elif (layer_type == "BatchNorm"):
        attribute_map["epsilon"] = float(layer_param.batch_norm_param.eps)

    elif (layer_type == "InnerProduct"):
        attribute_map["broadcast"] = 1
        attribute_map["transB"] = 1
    elif (layer_type == "ReLU"):
        relu = layer_param.relu_param
        slope = relu.negative_slope
        attribute_map["alpha"] = slope
            
    return attribute_map

# calculate dimensions of the output of each layer.
def calculateTensorDims(layer_param, input_map, attribute_map):
    dimList = {}
    output_dims = [0, 0, 0, 0]
    inputs = input_map.keys()
    if(layer_param.type == "Convolution"):
        strides = attribute_map["strides"]
        pads = attribute_map["pads"]
        dilations = attribute_map["dilations"]
        kernel_shape = attribute_map["kernel_shape"]
        n,c,h,w = input_map[inputs[0]]

        #output_dims[3] = (pads[0] + int(w) + pads[2] - ((kernel_shape[0] - 1) * dilations[0] + 1)) // strides[0] + 1
        #output_dims[2] = (pads[1] + int(h) + pads[3] - ((kernel_shape[1] - 1) * dilations[1] + 1)) // strides[1] + 1

        output_dims[3] = ((int(w) + 2 * pads[0] - kernel_shape[0] - (kernel_shape[0] - 1) * (dilations[0] - 1))// strides[0]) + 1
        output_dims[2] = ((int(h) + 2 * pads[1] - kernel_shape[1] - (kernel_shape[1] - 1) * (dilations[1] - 1))// strides[1]) + 1
        output_dims[1] = layer_param.convolution_param.num_output
        output_dims[0] = n

        weight_dims = [output_dims[1], c, kernel_shape[1], kernel_shape[0]]
        dimList["weights"] = weight_dims
        if (layer_param.convolution_param.bias_term):
            bias_dims = [weight_dims[0]]
            dimList["bias"] = bias_dims

    elif (layer_param.type == "Deconvolution"):
        strides = attribute_map["strides"]
        pads = attribute_map["pads"]
        dilations = attribute_map["dilations"]
        kernel_shape = attribute_map["kernel_shape"]
        n,c,h,w = input_map[str(inputs[0])]

        output_dims[3] = strides[0] * (w - 1) + dilations[0] * (kernel_shape[0] - 1) + 1 - (2 * pads[0])
        output_dims[2] = strides[1] * (h - 1) + dilations[1] * (kernel_shape[1] - 1) + 1 - (2 * pads[1])
        output_dims[1] = layer_param.convolution_param.num_output
        output_dims[0] = n

        weight_dims = [output_dims[1], c, kernel_shape[1] , kernel_shape[0]]
        dimList["weights"] = weight_dims
        if (layer_param.convolution_param.bias_term):
            bias_dims = [weight_dims[0]]
            dimList["bias"] = bias_dims

    elif (layer_param.type == "Pooling"):
        strides = attribute_map["strides"]
        pads = attribute_map["pads"]
        kernel_shape = attribute_map["kernel_shape"]
        n,c,h,w = input_map[str(inputs[0])]

        if (layer_param.pooling_param.global_pooling):
            kernel_shape[1] = h
            kernel_shape[0] = w
            pads[0] = 0
            pads[1] = 0
            strides[0] = 1
            strides[1] = 1
        #output_dims[3] = (pads[0] + int(w) + pads[2] - ((kernel_shape[0] - 1) * dilations[0] + 1)) // strides[0] + 1
        #output_dims[2] = (pads[1] + int(h) + pads[3] - ((kernel_shape[1] - 1) * dilations[1] + 1)) // strides[1] + 1

        output_dims[3] = int(math.ceil(float(w + 2 * pads[0] + strides[0] - kernel_shape[0])/strides[0]))
        output_dims[2] = int(math.ceil(float(h + 2 * pads[1] + strides[1] - kernel_shape[1])/strides[1]))
        if (pads[1] > 0):
            if (output_dims[2] - 1) * strides[1] >= (h + pads[1]):
                output_dims[2] = output_dims[2] - 1
        if (pads[0] > 0):
            if (output_dims[3] - 1) * strides[0] >= (w + pads[0]):
                output_dims[3] = output_dims[3] - 1

        output_dims[1] = c
        output_dims[0] = n

    elif (layer_param.type == "InnerProduct"):
        n,c,h,w = input_map[str(inputs[0])]
        output_dims[3] = 1
        output_dims[2] = 1
        output_dims[1] = layer_param.inner_product_param.num_output
        output_dims[0] = n

        weight_dims = [output_dims[1], c, h, w]
        dimList["weights"] = weight_dims
        if (layer_param.inner_product_param.bias_term):
            dimList["bias"] = [weight_dims[0]]

    elif (layer_param.type == "Concat"):
        inputs = input_map.keys()
        for i in range(len(inputs)):
            n,c,h,w = input_map[inputs[i]]
            output_dims[1] += c
        n,c,h,w = input_map[inputs[0]]
        output_dims[0] = n
        output_dims[2] = h
        output_dims[3] = w
    elif (layer_param.type == "BatchNorm" or layer_param.type == "Scale"):
        output_dims[0], output_dims[1], output_dims[2], output_dims[3] = input_map[str(inputs[0])]
        if (len(layer_param.blobs) > 0):
            weight_dims = [output_dims[1]]
            dimList["weights"] = weight_dims
        if (len(layer_param.blobs) > 1):
            bias_dims = [output_dims[1]]
            dimList["bias"] = bias_dims
    else:
        output_dims[0],output_dims[1],output_dims[2],output_dims[3] = input_map[str(inputs[0])]

    dimList["output"] = output_dims

    return dimList

# extract caffe node information into ir nodes.
def extractCaffeNodeInfo(net_parameter, graph, inputsInfo, verbose):
    inputOutputMap = {}
    dropoutLayerMap = {}
    splitLayerMap = {}
    outputNameAliasMap = {}
    inputsMap = {}
    outputsMap = {}
    count = 0

    layers = net_parameter.layer

    for i in range(len(layers)):
        layer_param = layers[i]
        layer_name = caffe_name_to_ir_name(str(layer_param.name))
        layer_type = str(layer_param.type)
        inputs = layer_param.bottom
        outputs = layer_param.top

        # ignoring the input/data layer as input is already obtained in previous step.
        if (layer_type == "Data" or layer_type == "ImageData" or layer_type == "Input"):
            continue

        # dropout layer is copy layer in inference, hence aliasing the input for dropout layer for next layer.
        if (layer_type == "Dropout"):
            in_name = caffe_name_to_ir_name(str(inputs[0]))
            if in_name in outputNameAliasMap:
                in_name = outputNameAliasMap[in_name]
            dropoutLayerMap[caffe_name_to_ir_name(str(outputs[0]))] = in_name
            continue

        # split layer optimization.
        if (layer_type == "Split"):
            in_name = caffe_name_to_ir_name(str(inputs[0]))
            if (in_name in outputNameAliasMap):
                in_name = outputNameAliasMap[in_name]
            for k in range(len(outputs)):
                splitLayerMap[caffe_name_to_ir_name(outputs[k])] = in_name
            continue

        layer_info_map = {}
        input_info_map = {}
        output_info_map = {}
        layer_info_map["layer_name"] = layer_name
        if layer_type in caffe2ir_op_type:
            layer_info_map["layer_type"] = caffe2ir_op_type[layer_type]
        elif layer_type == "Pooling":
            pool_type = layer_param.pooling_param.pool
            layer_info_map["layer_type"] = "max_pool" if (pool_type == caffe_pb2.PoolingParameter.MAX) else "avg_pool"

        #fusing scale layer to batchnorm layer.
        #adding scale weights and biases into the batchnorm, else fusing scale to mul or muladd operator.
        elif layer_type == "Scale":
            if (count > 0 and (count < len(layers))):
                prev_layer_info = inputOutputMap[count-1]
                prev_layer_type = prev_layer_info["layer_type"]
                if (prev_layer_type == "batch_norm"):
                    modified_out_info_map = {}
                    scale_weights_map = {}
                    scale_bias_map = {}
                    extractBinary(layer_param, graph, verbose)
                    prev_input_map = prev_layer_info["inputs"]
                    prev_attribute_map = prev_layer_info["attributes"]
                    dimList = calculateTensorDims(layer_param, prev_input_map, prev_attribute_map)
                    modified_out_info_map[layer_name] = dimList["output"]
                    outputsMap.update(modified_out_info_map)
                    prev_layer_info["outputs"] = modified_out_info_map
                    if ("weights" in dimList):
                        scale_weights = layer_name + "_w"
                        scale_weights_map[scale_weights] = dimList["weights"]
                        prev_layer_info["scale_weights"] = scale_weights_map
                        graph.addVariable(caffe_blob_to_ir_tensor(scale_weights, "F032", dimList["weights"]))
                    if ("bias" in dimList):
                        scale_bias = layer_name + "_b"
                        scale_bias_map[scale_bias] = dimList["bias"]
                        prev_layer_info["scale_bias"] = scale_bias_map
                        graph.addVariable(caffe_blob_to_ir_tensor(scale_bias, "F032", dimList["bias"]))
                    if(layer_name != caffe_name_to_ir_name(str(outputs[0]))):
                        outputNameAliasMap[caffe_name_to_ir_name(str(outputs[0]))] = layer_name
                    prev_layer_info["layer_name"] = layer_name
                    inputOutputMap[count - 1] = prev_layer_info
                    if (verbose):
                        print (prev_layer_info)
                    node = caffe_node_to_ir_node(prev_layer_info["layer_type"], prev_layer_info)
                    graph.addNode(node)
                    if (verbose):
                        print ("OK: fusing scale to batch_norm")
                    continue
                else:
                    scale_layer_type = 'mul' if len(layer_param.blobs) == 1 else 'muladd'
                    if (verbose):
                        print ("OK: Fusing scale to : " + scale_layer_type)
                    layer_info_map["layer_type"] = scale_layer_type
        else:
            print ("ERROR: caffe operation %s is not supported yet." % (layer_type))
            sys.exit(1)

        # extract attributes of the layer.
        attribute_map = extractCaffeAttrInfo(layer_param)
        layer_info_map["attributes"] = attribute_map
        if (layer_type == "ReLU" and attribute_map["alpha"] != 0):
            layer_info_map["layer_type"] = "leaky_relu"

        #extract input information.
        if (count == 0):
            for k in range(len(inputs)):
                in_name = caffe_name_to_ir_name(str(inputs[k]))
                if str(inputs[k]) in inputsInfo:
                    input_info_map[in_name] = inputsInfo[in_name]
                else:
                    print ("ERROR: unable to get the input dimensions for the layer %s" % (layer_name))
                    sys.exit(1)
        else:
            for k in range(len(inputs)):
                previous_layer_info = inputOutputMap[count - 1]
                prevOutMap = previous_layer_info["outputs"]
                input_name = str(caffe_name_to_ir_name(str(inputs[k])))

                # changing the name of the input based on alias name for top==bottom in previous layer.
                if (input_name in outputNameAliasMap):
                    input_name = outputNameAliasMap[input_name]

                if (input_name in splitLayerMap):
                    input_name = splitLayerMap[input_name]

                if (input_name in dropoutLayerMap):
                    input_name = dropoutLayerMap[input_name]

                # get the input dimensions.
                if input_name in prevOutMap:
                    input_info_map[input_name] = prevOutMap[input_name]
                elif input_name in outputsMap:
                    input_info_map[input_name] = outputsMap[input_name]
                elif input_name in inputsMap:
                    input_info_map[input_name] = inputsMap[input_name]
                elif input_name in dropoutLayerMap:
                    input_info_map[dropoutLayerMap[input_name]] = outputsMap[dropoutLayerMap[input_name]]
                elif input_name in splitLayerMap:
                    input_info_map[splitLayerMap[input_name]] = prevOutMap[splitLayerMap[input_name]]
                else:
                    if (((layer_type == "Softmax") or (layer_type == "SoftmaxWithLoss")) and k != 0):
                        break
                    elif input_name in outputNameAliasMap:
                        input_info_map[outputNameAliasMap[input_name]] = prevOutMap[outputNameAliasMap[input_name]]
                    else:
                        print ("ERROR: unknown dimensions for %s in the layer %s " % (input_name, layer_name))
                        sys.exit(1)

        inputsMap.update(input_info_map)

        #calculate output,weight and bias dimensions.
        dimList = calculateTensorDims(layer_param, input_info_map, attribute_map)
        if (len(outputs) > 0) and caffe_name_to_ir_name(str(layer_name)) != caffe_name_to_ir_name(str(outputs[0])):
            outputNameAliasMap[caffe_name_to_ir_name(str(outputs[0]))] = caffe_name_to_ir_name(str(layer_name))

        output_info_map[layer_name] = dimList["output"]
        outputsMap.update(output_info_map)

        # add inputs and outputs to layer info.
        layer_info_map["inputs"] = input_info_map
        layer_info_map["outputs"] = output_info_map

        # add weights and biases if present.
        #add weights and biases info if present into the layer info.
        extractBinary(layer_param, graph, verbose)
        weights = layer_name + '_w'
        biases = layer_name +  '_b'
        weights_map = {}
        bias_map = {}
        if "weights" in dimList:
            weights = layer_name + '_w'
            weight_dims = dimList["weights"]
            weights_map[weights] = weight_dims
            graph.addVariable(caffe_blob_to_ir_tensor(weights, "F032", weight_dims))
            layer_info_map["weights"] = weights_map
        if "bias" in dimList:
            biases = layer_name + "_b"
            bias_dims = dimList["bias"]
            bias_map[biases] = bias_dims
            graph.addVariable(caffe_blob_to_ir_tensor(biases, "F032", bias_dims))
            layer_info_map["biases"] = bias_map

        inputOutputMap[count] = layer_info_map
        count += 1

        if(layer_info_map["layer_type"] == "batch_norm" and (i < len(layers) - 1)):
            next_layer_param = layers[i+1]
            if(next_layer_param.type == "Scale"):
                continue

        if (verbose):
            print (layer_info_map)
        node = caffe_node_to_ir_node(layer_info_map["layer_type"], layer_info_map)
        graph.addNode(node)

    graph.updateLocals()
    return inputOutputMap


# convert caffe graph to ir graph.
def caffe_graph_to_ir_graph(net_parameter, input_dims, verbose):
    graph = IrGraph()
    inputMap = extractInput(net_parameter, graph, input_dims)
    inputOutputMap = extractCaffeNodeInfo(net_parameter, graph, inputMap, verbose)
    outputList = extractOutput(graph, inputOutputMap, verbose)
    return graph

# convert caffe representation to ir representation.
def caffe2ir(net_parameter, input_dims, outputFolder, verbose):
    if (len(net_parameter.layer) == 0):
        print ("ERROR: unsupported caffemodel, kindly upgrade your caffemodel.")
        sys.exit(1)
    graph = caffe_graph_to_ir_graph(net_parameter, input_dims, verbose)
    graph.toFile(outputFolder)
    print ("OK: graph successfully formed.")

def main():
    if len(sys.argv) < 4:
        print ("Usage : python caffe2nnir.py <caffeModel> <nnirOutputFolder> --input-dims n,c,h,w [--verbose 0|1]")
        sys.exit(1)
    caffeFileName = sys.argv[1]
    outputFolder = sys.argv[2]
    input_dims = sys.argv[4].split(',')

    verbose = 0
    if(len(sys.argv) > 5):
        verbose = 1 if int(sys.argv[6]) else 0
        if (verbose):
            print ("OK: verbose enabled.")

    print ("OK: loading caffemodel from %s ..." % (caffeFileName))
    net_parameter = caffe_pb2.NetParameter()
    if not os.path.isfile(caffeFileName):
        print ("ERROR: unable to open : " + caffeFileName)
        sys.exit(1)

    if (verbose):
        print ("parsing the caffemodel from : " + str(caffeFileName))
    net_parameter.ParseFromString(open(caffeFileName, 'rb').read())
    print ("OK: caffemodel read successful")
    print ("converting to AMD NNIR format in %s folder ... " % (outputFolder))
    if (verbose):
        print ("input parameters obtained are : " + str(input_dims[0]) + " " + str(input_dims[1]) + " " + str(input_dims[2]) + " " + str(input_dims[3]))

    caffe2ir(net_parameter, input_dims, outputFolder, verbose)

if __name__ == '__main__':
    main()
