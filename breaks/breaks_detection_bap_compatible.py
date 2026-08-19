import pandas as pd
import numpy as np
import scipy.stats as stats

# defs needed in the breaks code: dates for each band and output field names
metricnames = ['gid', 'PreChgDur', 'PreChgMag', 'PreChgEvl', 'ChgYr', 'ChgDur', 'ChgMag', 'ChgEvl', 'PstChgDur', 'PstChgMag', 'PstChgEvl']
metricnames = ['PreChgDur', 'PreChgMag', 'PreChgEvl', 'ChgYr', 'ChgDur', 'ChgMag', 'ChgEvl', 'PstChgDur', 'PstChgMag', 'PstChgEvl']

# This method takes the x and y time series as parameters, including a start and end index (numpy index).
# The mehtod returns the sum of the squared errors (SSE) of a linear model fit to the time series data confined 
# by the start and end parameters.
def calc_error(x_data, y_data, anchor, anchor_plus_i):
    
    #Get data as defined by anchor to anchor plus i from time series data
    years = x_data[anchor:anchor_plus_i]
    NBR = y_data[anchor:anchor_plus_i]
    
    
    #Use statsmodels to run the linear regression 
    slope, intercept, r_value, p_value, std_err = stats.linregress(years,NBR)
    
    sum_sqr_err = pow(std_err, 2) * (len(years) - 1)    

    #Return the SSE 
    return sum_sqr_err

# This method is similar to the calc_error method, however it returns the slope (beta) and intercept 
# of the linear model as a list instead of SSE.
def get_regress_params(x_data, y_data, start_idx, end_idx):
    
    #Get data as defined by start_idx to end_idx from time series data
    years = x_data[start_idx:end_idx]
    NBR = y_data[start_idx:end_idx]
    
    #Use statsmodels to run the linear regression 
    slope, intercept, r_value, p_value, std_err = stats.linregress(years,NBR)
    
    #Get Beta and Intercept 
    params = [slope, intercept]
    
    #Return model parameters 
    return params

# Simple method for getting the Y value associate with Y = MX+B
def get_y(slope, intercept, x):
    
    #Return Y value 
    return (slope*x)+intercept

# This method returns the slope and intercept for segments defined by [x1,y1] [x2,y2] as a list.
def create_segment(x1, y1, x2, y2):

    #Get Beta and Intercept 
    slope = (y1-y2)/(x1-x2)
    intcp = y1 - (slope*x1)
    
    #Return model parameters
    return [slope, intcp] 

# This method takes the x and y time series as input, as well as a list of (n/2) numpy array segment indices. 
# It returns a numpy array of costs (SSE) (length = n-1) associated with merging each segment with 
# the segment to its right.
def idex_merge_cost(x_data, y_data, seg_list):
    
    #Initialize a list of merge costs (SSE) between each segment
    merge_cost = np.arange(len(seg_list)-1, dtype=float)
    
    #Calculate cost (SSE) of merging each segment and the following segment 
    for i in range(len(seg_list)-1):
        
        #Get the start and end index of the proposed merge from the list of segments
        start = seg_list[i][0]
        end = seg_list[i+1][1]
        
        #Calculate the SSE cost of the proposed merge
        cost = float(calc_error(x_data, y_data, start, end))
        
        #Update the merge list with the SSE of the proposed merge 
        merge_cost[i] = cost
    
    #Return np array of merge costs (SSE's) 
    return merge_cost

# This method reads in the list of piecewise linear segments (output from Bottom Up), and inserts 
# the interpolated segment linear model parameters between breakpoints.
def intp_segments(seg_list):
    
    #Initialize a return list
    return_list = []
    
    #For each segment in seg_list
    for i in range(len(seg_list)):
        
        #Append the current segment to return list 
        return_list.append(seg_list[i])
        
        #If it is not the last segment 
        if i!=len(seg_list)-1:

            #Define the line between segments (breakpoint)
            x1 = seg_list[i][1]
            x2 = seg_list[i+1][0]
            y1 = get_y(seg_list[i][2],seg_list[i][3], seg_list[i][1])
            y2 = get_y(seg_list[i+1][2],seg_list[i+1][3], seg_list[i+1][0])

            #Get the interpolated line as a linear model
            intp_param = create_segment(x1,y1,x2,y2)

            #Create the segment 
            intp_seg = [seg_list[i][1],seg_list[i+1][0],intp_param[0],intp_param[1]]

            #Insert the interpolated segment into the return list  
            return_list.insert(i+1,intp_seg)
    
    #Return the original segments with the interpolated segments inserted
    return return_list

# This is the Bottom Up time series breakpoint algorithm copied from Keogh et al. 2001. 
# Given the x and y time series, and a SSE threshold, it will segment the time series into 
# piecewise linear fits between breakpoints, and return the segments as a list of linear model parameters 
# in the following order [start, end, slope, intercept] for each segment. Missing data MUST be excluded from the input.
def bottom_up(x_data, y_data, stderr_thresh):
    
    #List of segments
    seg_list = []
     
    #Check if there are even number of obs, if not drop first observation (1984) for Landsat time series 
    if len(x_data)%2 > 0:
        x_data = x_data[1:]
        y_data = y_data[1:]
            
    #Index the time series at finest scale, i.e., n/2
    for i in range(0,len(y_data),2):
        seg = [i,(i+1)]
        seg_list.append(seg)
            
    #List of the costs associated with merging each segment 
    merge_cost = idex_merge_cost(x_data, y_data, seg_list)
    
    #Dummy boolean
    SSE_Exceeded = False
    
    #While the minimum SSE's is below our threshold
    while SSE_Exceeded == False:
        
        #If there are no breakpoints (no change, merge_cost.size < 1)
        if merge_cost.size < 1:
            
            #Leave the loop
            break
        
        #Or if SSE threshold is exceeded (SSE_Exceeded == True)
        elif np.amin(merge_cost, axis=0) > stderr_thresh:
            #Leave the loop            
            break
        
        #Get the index of the 'cheapest' merge
        min_index = np.argmin(merge_cost)
        
        #Get the start end and dates of the individual segments, make a new merged segment
        start = seg_list[min_index][0]
        end = seg_list[min_index+1][1]
        
        #Create segment
        seg = [start,end]
        
        #Replace the segment at the 'cheapest' index with the merged segment 
        seg_list[min_index] = seg
        
        
        #Delete the old segments and merge cost  
        del seg_list[min_index+1]
        merge_cost = np.delete(merge_cost,min_index,axis=0)
        
        #If the 'cheapest' index is not at the end of the time series 
        if min_index+1<len(seg_list)-1:
            
            #Re-Calculate the new cost of merging with the following segment
            merge_cost[min_index] = calc_error(x_data, y_data, seg_list[min_index][0], seg_list[min_index+1][1])
        
        #If the 'cheapest' index is not equal to zero
        if min_index-1>=0:
            
            #Re-Calculate the cost of merging the prior segment with the new merged segment
            merge_cost[min_index-1] = calc_error(x_data, y_data, seg_list[min_index-1][0], seg_list[min_index][1])
    
    #Convert numpy indices to year for each segment, add slope and intercept to non-merged segments
    return_list = []
    
    #For each segment in seg_list
    for i in range(len(seg_list)):
        
        #Get the start end and dates of the individual segments, make a new merged segment
        start = seg_list[i][0]
        end = seg_list[i][1]
        
        #Get linear model parameters for segment
        reg_params = get_regress_params(x_data, y_data, start, end)
        beta = reg_params[0]
        intcp = reg_params[1]
        
        #Check that segment has been merged (i.e., duration > 1 year)
        if (end-start)>1:
            
            #Same segment but with years instead of indices, add slope and intercept of segment
            seg = [x_data[start], x_data[end],beta,intcp]
        
        #It has not been merged, (i.e., 1 year duration) 
        else:
            
            #Get slope and intercept of the line from x1,y1,x2,y2
            seg_param = create_segment(x_data[start], y_data[start], x_data[end],y_data[end])
            
            #Same segment but with years instead of indices, add slope and intercept
            seg = [x_data[start], x_data[end], seg_param[0], seg_param[1]]
        
        #Appednd new segment to return list
        return_list.append(seg)
    
    #Add interpolated breakpoint segments
    add_intp = intp_segments(return_list)
    
    #Return the final segmented indexes with linear model parameters 
    return add_intp

# This method is based on methods defined in Mass data processing of time series Landsat imagery:
# pixels to data products for forest monitoring, Hermosilla et al., 2016 for distilling forest change metrics 
# from time series of Landsat imagery. Using the piecewise segmented time series results, the method starts 
# by finding the segment with the greatest (most negative) change evolution. Once this segment is identified, 
# pre-change (preceding segment), change(change segment), and post change (following segment) metrics 
# including duration, magnitude and slope (evolution) are calculated. In addition, the year of change is output. 
# These metrics are then output as a list (see below).
def get_change_metrics(seg_list):
    #Initialize variable for index of maximum change (negative magnitude)
    max_chg_idx = 0
    
    #Initialize variable for value of maximum change (negative magnitude) set well above 1 (99 in this case)
    max_chg = 99
    
    #Initialize pre-change variables to missing 
    prechg_dur = float('nan')
    prechg_mag = float('nan')
    prechg_evl = float('nan')
    
    #Initialize change variables to missing 
    chg_dur = float('nan')
    chg_mag = float('nan')
    chg_evl = float('nan')
    chg_yr = float('nan')
    
    #Initialize post-change variables to missing 
    pstchg_dur = float('nan')
    pstchg_mag = float('nan')
    pstchg_evl = float('nan')
    
    #Boolean change indicator(currently not implimented) 
    #change_detected = False 
    
    #Coutner
    i=0
    
    #Foreach segment in the time series segment list
    for seg in seg_list:
        
        #Calculate the change in chg evolution
        seg_chg_evl = seg[2]
        
        #If it is less than the current 'maximum' change 
        if seg_chg_evl<max_chg:
            
            #Update max value and index 
            max_chg=seg_chg_evl
            max_chg_idx=i
        
        #Increment the counter 
        i = i+1
        
    #Check if there was a change, i.e., more than one segment
    if len(seg_list)>1:
    
        #########################
        ###Get Pre-Change Metrics 
        #########################

        #Check that there is pre-change data on record 
        if (max_chg_idx-1)>=0:

            #Get start and end year of pre-change segment 
            prechg_strt = seg_list[max_chg_idx-1][0]
            prechg_end = seg_list[max_chg_idx-1][1]

            #Calculate the pre-change duration 
            prechg_dur = prechg_end - prechg_strt

            #Get the NBR of the start and end of the pre-change segment 
            prechg_strt_y = get_y(seg_list[max_chg_idx-1][2],seg_list[max_chg_idx-1][3],seg_list[max_chg_idx-1][0])
            prechg_end_y = get_y(seg_list[max_chg_idx-1][2],seg_list[max_chg_idx-1][3],seg_list[max_chg_idx-1][1])

            #Calculate the change in magnitude of the pre-change segment   
            prechg_mag = prechg_end_y - prechg_strt_y

            #Get the evolution (slope) of pre-change segment 
            prechg_evl = seg_list[max_chg_idx-1][2]
        
        #########################
        ###Get Change Metrics 
        #########################
        
        #Get start and end year of change segment 
        chg_strt = seg_list[max_chg_idx][0]
        chg_end = seg_list[max_chg_idx][1]
        
        #Get the change year
        chg_yr = chg_end
        
        #Calculate the change duration 
        chg_dur = chg_end - chg_strt
        
        #Calculate the change in magnitude of the change segment 
        chg_strt_y = get_y(seg_list[max_chg_idx][2],seg_list[max_chg_idx][3],seg_list[max_chg_idx][0])
        chg_end_y = get_y(seg_list[max_chg_idx][2],seg_list[max_chg_idx][3],seg_list[max_chg_idx][1])
        
        chg_mag = chg_end_y-chg_strt_y 
        
        #Get the evolution (slope) of change segment 
        chg_evl = seg_list[max_chg_idx][2]
        
        #########################
        ###Get Post-Change Metrics 
        #########################
        
        #Check that there is post-change data on record, i.e., change did not occur at end of time series 
        if (max_chg_idx+1)<len(seg_list):
            
            #Get start and end year of post-change segment 
            pstchg_strt = seg_list[max_chg_idx+1][0]
            pstchg_end = seg_list[max_chg_idx+1][1]
            
            #Calculate the post-change duration 
            pstchg_dur = pstchg_end - pstchg_strt
        
            #Get the NBR of the start and end of the post-change segment 
            pstchg_strt_y = get_y(seg_list[max_chg_idx+1][2],seg_list[max_chg_idx+1][3],seg_list[max_chg_idx+1][0])
            pstchg_end_y = get_y(seg_list[max_chg_idx+1][2],seg_list[max_chg_idx+1][3],seg_list[max_chg_idx+1][1])

            #Calculate the change in magnitude of the post-change segment 
            pstchg_mag = pstchg_end_y - pstchg_strt_y
            
            #Get the evolution (slope) of post-change segment 
            pstchg_evl = seg_list[max_chg_idx+1][2]
     
    #Return the change metrics as a list 
    return [prechg_dur, prechg_mag, prechg_evl,chg_yr,chg_dur,chg_mag,chg_evl,pstchg_dur,pstchg_mag,pstchg_evl]
            
# call breaks code on numpy array
def getmetrics(x, y, thresh):
    bot_test = bottom_up(x, y, thresh)    
    changes = get_change_metrics(bot_test)
    return(changes)

# run breaks code
def callbreak(y_in, minyear=1984, maxyear=2022, threshold=0.025):
    x_in = np.array(range(minyear, maxyear+1))
    brk = getmetrics(x_in, y_in, threshold) # Break threshold of 0.025
    
    res = pd.DataFrame(brk).transpose()
    res.columns = metricnames
    return res
