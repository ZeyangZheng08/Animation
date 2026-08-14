using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations.Rigging;

public class NurseIKHelper : MonoBehaviour
{
    [Header("IK Setup")]
	[Tooltip("Assign items from Nurse IK Rig")]
	// Get Nurse IK object
	[SerializeField] private GameObject IK;
	[SerializeField] private Transform LeftHand;
	[SerializeField] private Transform LeftHint;
	[SerializeField] private Transform RightHand;
	[SerializeField] private Transform RightHint;

	[Header("Snap Speed")]
	[Tooltip("How fast does the IK pull to the target")]
	[SerializeField] private float weightChangeSpeed;

    [Header("IK Target Groups")]
    [Tooltip("Each group should contain Target & Hint")]
	// Get hand target positions
	[SerializeField] private Transform laptop;
	[SerializeField] private Transform pulseLeft;
    [SerializeField] private Transform pulseRight;
	[SerializeField] private Transform bvm;
    [SerializeField] private Transform aspirinLeft;
    [SerializeField] private Transform aspirinRight;
    [SerializeField] private Transform pickUp;

	// Get Target and Hint
	Transform LeftHandTarget;
	Transform LeftHintTarget;
	Transform RightHandTarget;
	Transform RightHintTarget;

    // Get Left Arm weights
    float leftHandWeight;
    float prevLeftWeight;

    // Get Right Arm weights
    float rightHandWeight;
    float prevRightWeight;

    // Determine whether to track or not
    bool trackLeftIK;
    bool trackRightIK;

    void Start()
    {
		// Assign weights
        leftHandWeight = LeftHand.GetComponent<TwoBoneIKConstraint>().weight;
		prevLeftWeight = leftHandWeight;
		rightHandWeight = RightHand.GetComponent<TwoBoneIKConstraint>().weight;
		prevRightWeight = rightHandWeight;

        // Reset trackIK
        trackLeftIK = false;
        trackRightIK = false;
	}

	void Update()
    {
        // Set weights that have changed
        if (prevLeftWeight != leftHandWeight)
        {
            prevLeftWeight = SetWeights(prevLeftWeight, leftHandWeight);
            LeftHand.GetComponent<TwoBoneIKConstraint>().weight = prevLeftWeight;
        }
        if (prevRightWeight != rightHandWeight)
        {
            prevRightWeight = SetWeights(prevRightWeight, rightHandWeight);
            RightHand.GetComponent<TwoBoneIKConstraint>().weight = prevRightWeight;
        }

        if (trackLeftIK)
        {
            SetIKPoints("left");
        }
        if (trackRightIK)
        {
            SetIKPoints("right");
        }
	}

    // Sets the new IK targets
    // Expects order:
    // 1 - LeftHand
    // 2 - LeftHint
    // 3 - RightHand
    // 4 - RightHint
    // NOTE: The Hand obj should always be above the corresponding Hint obj
    void GetIKPoints(Transform parent, string hands)
    {
        if (hands == "left")
        {
            LeftHandTarget = parent.GetChild(0);
            LeftHintTarget = parent.GetChild(1);
        } else if (hands == "right")
        {
			RightHandTarget = parent.GetChild(0);
			RightHintTarget = parent.GetChild(1);
		}
        else if (hands == "both")
        {
			LeftHandTarget = parent.GetChild(0);
			LeftHintTarget = parent.GetChild(1);
			RightHandTarget = parent.GetChild(2);
			RightHintTarget = parent.GetChild(3);
		}
    }

    void SetIKPoints(string hands)
    {
        if (hands == "left" || hands == "both")
        {
            if (LeftHandTarget == null) { trackLeftIK = false; return; }
            LeftHand.transform.position = LeftHandTarget.position;
            LeftHand.transform.rotation = LeftHandTarget.rotation;
            LeftHint.transform.position = LeftHintTarget.position;
        }
        if (hands == "right" || hands == "both")
        {
            if (RightHandTarget == null) { trackRightIK = false; return; }
            RightHand.transform.position = RightHandTarget.position;
            RightHand.transform.rotation = RightHandTarget.rotation;
            RightHint.transform.position = RightHintTarget.position;
        }
	}

    float SetWeights(float prevWeight, float newWeight)
    {
        float diff = Mathf.Abs(prevWeight - newWeight);
		// Lerp weight up to specified amount
		if (diff != 0)
		{
            if(diff < weightChangeSpeed)
            {
                prevWeight = newWeight;
            }
            else if (prevWeight < newWeight)
			{
                prevWeight += weightChangeSpeed; 
			}
            else if (prevWeight > newWeight)
			{
				prevWeight -= weightChangeSpeed;
			}
		}
		return prevWeight;
	}

    // Sets the IK weights back to zero
    public void ResetHandsIK(float weightSpeed)
    {
        trackLeftIK = false;
        trackRightIK = false;
		weightChangeSpeed = weightSpeed;
		leftHandWeight = 0f;
        rightHandWeight = 0f;
    }

    // Resets just the left hand IK for staggered timing
	public void ResetLeftHandIK(float weightSpeed)
	{
		trackLeftIK = false;
		weightChangeSpeed = weightSpeed;
		leftHandWeight = 0f;
	}

    // Resets just the right hand IK for staggered timing
	public void ResetRightHandIK(float weightSpeed)
	{
		trackRightIK = false;
		weightChangeSpeed = weightSpeed;
		rightHandWeight = 0f;
	}

	public void SetIKSystemWeight(float weight)
    {
		IK.GetComponent<Rig>().weight = weight;
	}

    //Handles nurse typing on the laptop
    public void LaptopIK(float weightSpeed)
    {
		weightChangeSpeed = weightSpeed;

		GetIKPoints(laptop, "both");
        SetIKPoints("both");

        leftHandWeight = 1f;
        rightHandWeight = 1f;
    }

    //Handles the nurse checking the patient's pulse either conscious or unconscious
    public void PulseIK(float weightSpeed)
    {
        trackLeftIK = true;
        trackRightIK = true;
		weightChangeSpeed = weightSpeed;
		
        GetIKPoints(pulseLeft, "left");
        GetIKPoints(pulseRight, "right");
        SetIKPoints("both");

        leftHandWeight = 1f;
        rightHandWeight = 1f;
    }

    public void BVMIK(float weightSpeed)
    {
        trackLeftIK = true;
        trackRightIK = true;
		weightChangeSpeed = weightSpeed;

		GetIKPoints(bvm, "both");
		SetIKPoints("both");

		leftHandWeight = 1f;
        rightHandWeight = 1f;
	}

    // Handles the nurse giving the patient oral medication
    // Aspirin IKs separated into two functions to stagger IK activation times
    // AspirinLeft group parented under patient CC_Base_L_Hand
    public void AspirinLeftHandIK(float weightSpeed)
    {
        trackLeftIK = true;
        weightChangeSpeed = weightSpeed;

        GetIKPoints(aspirinLeft, "left");
        SetIKPoints("left");

        leftHandWeight = 1f;
    }
	// AspirinRight group parented under patient CC_Base_R_Hand
	public void AspirinRightHandIK(float weightSpeed)
    {
        trackRightIK = true;
        weightChangeSpeed = weightSpeed;

        GetIKPoints(aspirinRight, "right");
        SetIKPoints("right");

        rightHandWeight = 1f;
    }

    // Handles the nurse picking up the medicine bottle
    // PickUp group parented under Aspirin Bottle at MedCabinet
    public void PickUpIK(float weightSpeed)
    {
        trackRightIK = true;
        weightChangeSpeed = weightSpeed;

        GetIKPoints(pickUp, "right");
        SetIKPoints("right");

        rightHandWeight = 1f;
    }
}